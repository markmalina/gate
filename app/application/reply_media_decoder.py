"""Decode untrusted PNG/JPEG in a bounded child process with a binary-only pipe."""

from __future__ import annotations

import asyncio
import io
import struct
import sys
import threading
import warnings
import zlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MAX_MEDIA_BYTES = 8 * 1024 * 1024
MAX_MEDIA_SIDE = 4096
MAX_MEDIA_DIMENSION_SUM = 8192
MAX_MEDIA_PIXELS = 16_000_000
MAX_MEDIA_ASPECT_RATIO = 20
DECODE_MEMORY_BYTES = 384 * 1024 * 1024
DECODE_CPU_SECONDS = 3
DECODE_TIMEOUT_SECONDS = 5
DECODE_SLOTS = 2
_SLOTS = threading.BoundedSemaphore(DECODE_SLOTS)
_CLEANUPS: set[asyncio.Task[None]] = set()
_HEADER = struct.Struct(">IHHBB")


class _ImageRejection(StrEnum):
    UNSAFE = "Image decode failed or image is unsafe"
    ENCODING = "Unsupported image encoding or animation"
    DIMENSIONS = "Image exceeds application dimensions, pixels or aspect limits"
    NORMALIZED_SIZE = "Normalized image exceeds 8 MiB"
    PNG_SIGNATURE = "PNG signature mismatch"
    PNG_TRUNCATED = "Truncated or animated PNG"
    PNG_CRC = "Invalid PNG chunk CRC"
    PNG_TRAILING = "PNG has trailing or invalid data"
    PNG_END = "PNG has no complete end marker"
    JPEG_SIGNATURE = "JPEG signature mismatch"
    JPEG_MARKER = "Invalid JPEG marker"
    JPEG_TRAILING = "JPEG has trailing data"
    JPEG_TRUNCATED = "Truncated JPEG"


_REJECTIONS = tuple(_ImageRejection)


class ImageDecodeError(Exception):
    def __init__(self, message: str, code: str = "invalid_media") -> None:
        super().__init__(message)
        self.code = code


class _RejectedImage(ValueError):
    def __init__(self, reason: _ImageRejection) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DecodedImage:
    data: bytes
    width: int
    height: int
    orientation: int
    colorspace: str


async def decode_image(source: bytes, media_type: str) -> DecodedImage:
    if not _SLOTS.acquire(blocking=False):
        raise ImageDecodeError("Image validation is busy; prepare later", "media_validation_busy")
    # Own the spawn even when the caller is cancelled before it gets a process handle.
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).with_name("reply_media_decoder.py")),
            media_type,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
    )
    try:
        async with asyncio.timeout(DECODE_TIMEOUT_SECONDS):
            process = await asyncio.shield(spawn)
            return await _exchange(process, source)
    except TimeoutError as exc:
        raise ImageDecodeError(
            "Image validation exceeded its deadline", "media_validation_timeout"
        ) from exc
    except (OSError, asyncio.IncompleteReadError) as exc:
        raise ImageDecodeError(_ImageRejection.UNSAFE) from exc
    finally:
        cleanup = asyncio.create_task(_reap(spawn))
        _CLEANUPS.add(cleanup)
        cleanup.add_done_callback(_CLEANUPS.discard)
        await asyncio.shield(cleanup)


async def _exchange(process: asyncio.subprocess.Process, source: bytes) -> DecodedImage:
    stdin, stdout = process.stdin, process.stdout
    assert stdin is not None and stdout is not None
    try:
        stdin.write(source)
        await stdin.drain()
    finally:
        stdin.close()
    status = (await stdout.readexactly(1))[0]
    if status:
        if status > len(_REJECTIONS) or await stdout.read(1) or await process.wait():
            raise ImageDecodeError(_ImageRejection.UNSAFE)
        raise ImageDecodeError(_REJECTIONS[status - 1])
    size, width, height, orientation, alpha = _HEADER.unpack(
        await stdout.readexactly(_HEADER.size)
    )
    if not 0 < size <= MAX_MEDIA_BYTES or alpha not in (0, 1):
        raise ImageDecodeError(_ImageRejection.UNSAFE)
    data = await stdout.readexactly(size)
    if await stdout.read(1) or await process.wait():
        raise ImageDecodeError(_ImageRejection.UNSAFE)
    return DecodedImage(data, width, height, orientation, "RGBA" if alpha else "RGB")


async def _reap(spawn: asyncio.Task[asyncio.subprocess.Process]) -> None:
    try:
        try:
            process = await spawn
        except OSError:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Drain an already-paused stdout transport after killing the writer so
        # subprocess.wait() can finish, even if validation stopped mid-frame.
        if process.stdout is not None:
            while await process.stdout.read(64 * 1024):
                pass
        await process.wait()
    finally:
        _SLOTS.release()


def _worker(media_type: str) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (DECODE_MEMORY_BYTES, DECODE_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (DECODE_CPU_SECONDS, DECODE_CPU_SECONDS))
    try:
        source = sys.stdin.buffer.read(MAX_MEDIA_BYTES + 1)
        if not 0 < len(source) <= MAX_MEDIA_BYTES:
            raise _RejectedImage(_ImageRejection.UNSAFE)
        decoded = _decode(source, media_type)
    except _RejectedImage as exc:
        status = _REJECTIONS.index(exc.reason) + 1
        sys.stdout.buffer.write(bytes((status,)))
        return
    sys.stdout.buffer.write(b"\x00")
    sys.stdout.buffer.write(
        _HEADER.pack(
            len(decoded.data), decoded.width, decoded.height,
            decoded.orientation, int(decoded.colorspace == "RGBA"),
        )
    )
    sys.stdout.buffer.write(decoded.data)


def _decode(source: bytes, media_type: str) -> DecodedImage:
    from PIL import Image, ImageOps

    if media_type == "image/png":
        _validate_png_container(source)
    elif media_type == "image/jpeg":
        _validate_jpeg_container(source)
    else:
        raise _RejectedImage(_ImageRejection.ENCODING)

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(source)) as check:
                expected_format = "PNG" if media_type == "image/png" else "JPEG"
                if check.format != expected_format or getattr(check, "n_frames", 1) != 1:
                    raise _RejectedImage(_ImageRejection.ENCODING)
                _validate_dimensions(*check.size)
                check.verify()
            with Image.open(io.BytesIO(source)) as original:
                original.load()
                raw_orientation = original.getexif().get(274, 1)
                orientation = raw_orientation if type(raw_orientation) is int else 1
                image = ImageOps.exif_transpose(original)
                _validate_dimensions(*image.size)
                # Re-encode before consent to remove metadata; never crop or resize.
                alpha = media_type == "image/png" and (
                    "A" in image.getbands() or "transparency" in image.info
                )
                colorspace = "RGBA" if alpha else "RGB"
                image = image.convert(colorspace)
                image.info.clear()
                output = io.BytesIO()
                if media_type == "image/png":
                    image.save(output, format="PNG", compress_level=9, optimize=False)
                else:
                    image.save(output, format="JPEG", quality=95, subsampling=0, optimize=False)
                data = output.getvalue()
                width, height = image.size
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise _RejectedImage(_ImageRejection.DIMENSIONS) from exc
    if not 0 < len(data) <= MAX_MEDIA_BYTES:
        raise _RejectedImage(_ImageRejection.NORMALIZED_SIZE)
    orientation = orientation if 1 <= orientation <= 8 else 1
    return DecodedImage(data, width, height, orientation, colorspace)


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or max(width, height) > MAX_MEDIA_SIDE
        or width + height > MAX_MEDIA_DIMENSION_SUM
        or width * height > MAX_MEDIA_PIXELS
        or max(width, height) > min(width, height) * MAX_MEDIA_ASPECT_RATIO
    ):
        raise _RejectedImage(_ImageRejection.DIMENSIONS)


def _validate_png_container(data: bytes) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _RejectedImage(_ImageRejection.PNG_SIGNATURE)
    position = 8
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data) or kind == b"acTL":
            raise _RejectedImage(_ImageRejection.PNG_TRUNCATED)
        crc = int.from_bytes(data[end - 4 : end], "big")
        if zlib.crc32(data[position + 4 : end - 4]) & 0xFFFFFFFF != crc:
            raise _RejectedImage(_ImageRejection.PNG_CRC)
        position = end
        if kind == b"IEND":
            if length != 0 or position != len(data):
                raise _RejectedImage(_ImageRejection.PNG_TRAILING)
            return
    raise _RejectedImage(_ImageRejection.PNG_END)


def _validate_jpeg_container(data: bytes) -> None:
    if not data.startswith(b"\xff\xd8"):
        raise _RejectedImage(_ImageRejection.JPEG_SIGNATURE)
    position = 2
    entropy = False
    while position < len(data):
        if entropy:
            position = data.find(b"\xff", position)
            if position < 0:
                break
        elif data[position] != 255:
            raise _RejectedImage(_ImageRejection.JPEG_MARKER)
        while position < len(data) and data[position] == 255:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        entropy = False
        if marker == 0xD9:
            if position != len(data):
                raise _RejectedImage(_ImageRejection.JPEG_TRAILING)
            return
        if marker in {0, 0xD8}:
            raise _RejectedImage(_ImageRejection.JPEG_MARKER)
        if marker == 1 or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            break
        position += length
        entropy = marker == 0xDA
    raise _RejectedImage(_ImageRejection.JPEG_TRUNCATED)


if __name__ == "__main__":
    try:
        _worker(sys.argv[1])
    except Exception:
        # A failed worker may have no frame; the parent reports a generic error.
        sys.exit(1)
