"""Validate a complete image package before any persistence or external effect."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import re
import struct
import unicodedata
import warnings
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL import __version__ as pillow_version

from app.application.reply_drafts import ReplyDraftError
from app.application.telegram_formatting import FormattingError, compile_text
from app.core.mcp_security import Principal

MAX_MEDIA_BYTES = 8 * 1024 * 1024
MAX_MEDIA_PACKAGE_BYTES = 24 * 1024 * 1024
MAX_MEDIA_BASE64_LENGTH = ((MAX_MEDIA_BYTES + 2) // 3) * 4
MAX_MEDIA_ITEMS = 10
MAX_MEDIA_SIDE = 4096
MAX_MEDIA_DIMENSION_SUM = 8192
MAX_MEDIA_PIXELS = 16_000_000
MAX_MEDIA_ASPECT_RATIO = 20
MAX_MEDIA_CAPTION_SOURCE_LENGTH = 4096
MEDIA_PACKAGE_KEY = "_gate_media_package"
MEDIA_SCHEMA_VERSION = "gate.media.v1"
MEDIA_CONVERTER_VERSION = "gate.media-package.v1"
MEDIA_TRANSFORM_VERSION = f"gate.image-normalize.v1;pillow-{pillow_version}"
MEDIA_MODES = frozenset({"photo", "album", "rich_photo", "rich_slideshow", "rich_collage"})
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class ReplyMediaInput:
    filename: str
    media_type: str
    content_base64: str
    expected_sha256: str
    caption: str = ""
    caption_format: str = "plain"


@dataclass(frozen=True, slots=True)
class PrepareReplyMediaCommand:
    principal: Principal
    task_id: UUID
    mode: str
    items: tuple[ReplyMediaInput, ...]
    staging_key: str
    execution_id: str
    execution_expires_at: datetime
    follow_up: bool = False
    rich_content: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ValidatedMediaItem:
    filename: str
    media_type: str
    source_size_bytes: int
    size_bytes: int
    source_sha256: str
    output_sha256: str
    width: int
    height: int
    transform_version: str
    transform: str
    caption: str
    caption_format: str
    caption_formatting_json: dict[str, Any]
    data: bytes


@dataclass(frozen=True, slots=True)
class PreparedMediaItem:
    media_ref: str
    ordinal: int
    filename: str
    media_type: str
    source_size_bytes: int
    size_bytes: int
    source_sha256: str
    output_sha256: str
    width: int
    height: int
    transform_version: str
    transform: str
    caption: str
    caption_format: str
    caption_formatting_json: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_ref": self.media_ref,
            "version": 1,
            "ordinal": self.ordinal,
            "filename": self.filename,
            "media_type": self.media_type,
            "source_size_bytes": self.source_size_bytes,
            "size_bytes": self.size_bytes,
            "source_sha256": self.source_sha256,
            "output_sha256": self.output_sha256,
            "width": self.width,
            "height": self.height,
            "transform_version": self.transform_version,
            "transform": self.transform,
            "caption": self.caption,
            "caption_format": self.caption_format,
            "caption_formatting_json": self.caption_formatting_json,
        }


@dataclass(frozen=True, slots=True)
class PreparedReplyMedia:
    draft_id: UUID
    package_id: UUID
    task_id: UUID
    account_label: str
    recipient_chat_id: str
    mode: str
    package_sha256: str
    expected_message_count: int
    expires_at: datetime
    items: tuple[PreparedMediaItem, ...]
    rich_content: dict[str, Any] | None
    execution_id: str
    execution_expires_at: datetime
    idempotent_replay: bool = False


class ReplyMediaWriter(Protocol):
    async def save(
        self,
        command: PrepareReplyMediaCommand,
        items: tuple[ValidatedMediaItem, ...],
        input_sha256: str,
    ) -> PreparedReplyMedia: ...


def media_package_digest(snapshot: dict[str, Any]) -> str:
    content = {key: value for key, value in snapshot.items() if key != "package_sha256"}
    return hashlib.sha256(_json(content).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _error(message: str, code: str = "invalid_media") -> ReplyDraftError:
    return ReplyDraftError(code=code, message=message)


class PrepareReplyMediaService:
    def __init__(self, writer: ReplyMediaWriter) -> None:
        self._writer = writer

    async def execute(self, command: PrepareReplyMediaCommand) -> PreparedReplyMedia:
        if "gate:drafts:write" not in command.principal.scopes:
            raise _error("Scope gate:drafts:write is required", "insufficient_scope")
        scope = command.principal.access_scope
        if not scope.is_explicit:
            raise _error(
                "Explicit workspace, account and project grants are required",
                "invalid_access_scope",
            )
        if not 0 < len(scope.workspace_id) <= 255 or not 0 < len(command.principal.subject) <= 255:
            raise _error("Invalid media principal identity", "invalid_access_scope")
        if not _KEY.fullmatch(command.staging_key) or not _KEY.fullmatch(command.execution_id):
            raise _error("Invalid staging or execution key")
        deadline = command.execution_expires_at
        now = datetime.now(UTC)
        if (
            not isinstance(deadline, datetime)
            or deadline.tzinfo is None
            or deadline.utcoffset() is None
            or not now < deadline <= now + timedelta(hours=48)
        ):
            raise _error(
                "Execution deadline must be explicit, future and within 48 hours",
                "invalid_execution_expiry",
            )
        if command.mode not in MEDIA_MODES:
            raise _error("Unsupported media mode")
        count = len(command.items)
        if not 1 <= count <= MAX_MEDIA_ITEMS:
            raise _error("A package requires 1–10 images")
        if command.mode in {"photo", "rich_photo"} and count != 1:
            raise _error("This mode requires exactly one image")
        if command.mode in {"album", "rich_slideshow", "rich_collage"} and count < 2:
            raise _error("This mode requires at least two images")
        if command.mode.startswith("rich_") != (command.rich_content is not None):
            raise _error("Rich media modes require rich_content; ordinary modes prohibit it")
        if command.rich_content is not None:
            try:
                encoded_source = _json(command.rich_content)
            except (TypeError, ValueError, RecursionError) as exc:
                raise _error("Invalid Rich media content") from exc
            if len(encoded_source.encode("utf-8")) > 65536:
                raise _error("Rich media source exceeds 64 KiB")
        # All bytes and captions are validated before the writer can touch SQL.
        validated_items: list[ValidatedMediaItem] = []
        source_total = output_total = 0
        for source_item in command.items:
            item = _validate_image(source_item)
            source_total += item.source_size_bytes
            output_total += item.size_bytes
            if source_total > MAX_MEDIA_PACKAGE_BYTES:
                raise _error("Source package exceeds 24 MiB")
            if output_total > MAX_MEDIA_PACKAGE_BYTES:
                raise _error("Prepared package exceeds 24 MiB")
            validated_items.append(item)
        validated = tuple(validated_items)
        if command.mode.startswith("rich_") and any(item.caption for item in validated):
            raise _error("Rich captions belong in rich_content, not ordinary item captions")
        input_sha256 = hashlib.sha256(
            _json(
                {
                    "schema_version": MEDIA_SCHEMA_VERSION,
                    "converter_version": MEDIA_CONVERTER_VERSION,
                    "task_id": str(command.task_id),
                    "mode": command.mode,
                    "execution_id": command.execution_id,
                    "execution_expires_at": deadline.astimezone(UTC).isoformat(),
                    "follow_up": command.follow_up,
                    "items": [
                        {
                            "filename": item.filename,
                            "media_type": item.media_type,
                            "source_sha256": item.source_sha256,
                            "caption": original.caption,
                            "caption_format": original.caption_format,
                        }
                        for item, original in zip(validated, command.items, strict=True)
                    ],
                    "rich_content": command.rich_content,
                }
            ).encode("utf-8")
        ).hexdigest()
        return await self._writer.save(command, validated, input_sha256)


def _validate_image(item: ReplyMediaInput) -> ValidatedMediaItem:
    filename = item.filename
    if (
        not filename
        or filename in {".", ".."}
        or PurePosixPath(filename).name != filename
        or "\\" in filename
        or any(unicodedata.category(char).startswith("C") for char in filename)
        or len(filename.encode("utf-8")) > 255
    ):
        raise _error("Invalid image filename")
    types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if types.get(PurePosixPath(filename).suffix.lower()) != item.media_type:
        raise _error("Image filename and MIME mismatch")
    if len(item.expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in item.expected_sha256
    ):
        raise _error("Invalid image SHA-256")
    if (
        not isinstance(item.content_base64, str)
        or not item.content_base64
        or len(item.content_base64) > MAX_MEDIA_BASE64_LENGTH
    ):
        raise _error("Image exceeds 8 MiB")
    try:
        source = base64.b64decode(item.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _error("Invalid image encoding") from exc
    if not 0 < len(source) <= MAX_MEDIA_BYTES:
        raise _error("Image size must be 1 byte–8 MiB")
    source_sha256 = hashlib.sha256(source).hexdigest()
    if not hmac.compare_digest(source_sha256, item.expected_sha256):
        raise _error("Image SHA-256 differs", "media_changed")
    if item.media_type == "image/png":
        _validate_png_container(source)
    else:
        _validate_jpeg_container(source)
    if item.caption_format not in {"plain", "markdown"}:
        raise _error("Caption format must be plain or markdown")
    if not isinstance(item.caption, str) or len(item.caption) > MAX_MEDIA_CAPTION_SOURCE_LENGTH:
        raise _error("Caption source exceeds 4096 characters")
    try:
        caption = compile_text(item.caption, item.caption_format, max_length=1024, allow_empty=True)
    except FormattingError as exc:
        raise ReplyDraftError(code=exc.code, message=exc.message) from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as check:
                expected_format = "PNG" if item.media_type == "image/png" else "JPEG"
                if check.format != expected_format or getattr(check, "n_frames", 1) != 1:
                    raise _error("Unsupported image encoding or animation")
                _validate_dimensions(*check.size)
                check.verify()
            with Image.open(io.BytesIO(source)) as original:
                original.load()
                orientation = original.getexif().get(274, 1)
                image = ImageOps.exif_transpose(original)
                _validate_dimensions(*image.size)
                # Re-encode before consent: removes EXIF/IPTC/comments/ICC and makes the
                # transmitted artifact deterministic. Never crop or resize the image.
                wanted_mode = (
                    "RGBA" if item.media_type == "image/png" and "A" in image.getbands() else "RGB"
                )
                if (
                    item.media_type == "image/png"
                    and image.mode == "P"
                    and "transparency" in image.info
                ):
                    wanted_mode = "RGBA"
                image = image.convert(wanted_mode)
                image.info.clear()
                output = io.BytesIO()
                if item.media_type == "image/png":
                    image.save(output, format="PNG", compress_level=9, optimize=False)
                else:
                    image.save(output, format="JPEG", quality=95, subsampling=0, optimize=False)
                data = output.getvalue()
                width, height = image.size
    except ReplyDraftError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        struct.error,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise _error("Image decode failed or image is unsafe") from exc
    if not 0 < len(data) <= MAX_MEDIA_BYTES:
        raise _error("Normalized image exceeds 8 MiB")
    return ValidatedMediaItem(
        filename=filename,
        media_type=item.media_type,
        source_size_bytes=len(source),
        size_bytes=len(data),
        source_sha256=source_sha256,
        output_sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
        transform_version=MEDIA_TRANSFORM_VERSION,
        transform=f"metadata_strip;orientation={orientation};colorspace={wanted_mode};reencode",
        caption=caption.text,
        caption_format=item.caption_format,
        caption_formatting_json=caption.to_dict(),
        data=data,
    )


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or max(width, height) > MAX_MEDIA_SIDE
        or width + height > MAX_MEDIA_DIMENSION_SUM
        or width * height > MAX_MEDIA_PIXELS
        or max(width, height) > min(width, height) * MAX_MEDIA_ASPECT_RATIO
    ):
        raise _error("Image exceeds application dimensions, pixels or aspect limits")


def _validate_png_container(data: bytes) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _error("PNG signature mismatch")
    position = 8
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data) or kind == b"acTL":
            raise _error("Truncated or animated PNG")
        crc = int.from_bytes(data[end - 4 : end], "big")
        if zlib.crc32(data[position + 4 : end - 4]) & 0xFFFFFFFF != crc:
            raise _error("Invalid PNG chunk CRC")
        position = end
        if kind == b"IEND":
            if length != 0 or position != len(data):
                raise _error("PNG has trailing or invalid data")
            return
    raise _error("PNG has no complete end marker")


def _validate_jpeg_container(data: bytes) -> None:
    if not data.startswith(b"\xff\xd8"):
        raise _error("JPEG signature mismatch")
    position = 2
    entropy = False
    while position < len(data):
        if entropy:
            position = data.find(b"\xff", position)
            if position < 0:
                break
        elif data[position] != 255:
            raise _error("Invalid JPEG marker")
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
                raise _error("JPEG has trailing data")
            return
        if marker in {0, 0xD8}:
            raise _error("Invalid JPEG marker")
        if marker == 1 or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            break
        position += length
        entropy = marker == 0xDA
    raise _error("Truncated JPEG")
