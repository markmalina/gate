"""Original document checks. Never convert, extract to disk, or execute content."""

from __future__ import annotations

import asyncio
import io
import struct
import sys
import warnings
import zipfile
from pathlib import PurePosixPath
from typing import cast
from xml.etree import ElementTree

DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
DOCUMENT_MIME_TYPES = sorted(set(DOCUMENT_TYPES.values()))
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BASE64_LENGTH = ((MAX_DOCUMENT_BYTES + 2) // 3) * 4
VALIDATION_TIMEOUT_SECONDS = 5
VALIDATION_MEMORY_BYTES = 384 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_FRAMES = 64
_VALIDATION_SLOTS = asyncio.Semaphore(2)
_OOXML = {
    ".xlsx": ("xl/workbook.xml", "spreadsheetml.sheet.main+xml", "workbook"),
    ".docx": ("word/document.xml", "wordprocessingml.document.main+xml", "document"),
    ".pptx": ("ppt/presentation.xml", "presentationml.presentation.main+xml", "presentation"),
}


async def check_document_content(filename: str, data: bytes) -> None:
    """Isolate parser memory/CPU and bound queued work and cancellation cleanup."""
    from app.application.reply_drafts import ReplyDraftError

    try:
        await asyncio.wait_for(_VALIDATION_SLOTS.acquire(), timeout=0.1)
    except TimeoutError as exc:
        raise ReplyDraftError(
            code="document_validation_busy", message="Document validation is busy; prepare later"
        ) from exc
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.application.document_formats", filename,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.communicate(data), timeout=VALIDATION_TIMEOUT_SECONDS)
        if process.returncode != 0:
            raise ReplyDraftError(
                code="invalid_document", message="Document content type or structure mismatch"
            )
    except TimeoutError as exc:
        raise ReplyDraftError(
            code="document_validation_timeout", message="Document validation exceeded its limit"
        ) from exc
    finally:
        try:
            if process is not None and process.returncode is None:
                process.kill()
                await asyncio.shield(process.wait())
        finally:
            _VALIDATION_SLOTS.release()


def _xml(data: bytes) -> ElementTree.Element:
    from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

    # No DTD/entities/external resolution, including UTF-16 XML documents.
    return cast(
        ElementTree.Element,
        fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True),
    )


def _check_ooxml(data: bytes, extension: str) -> None:
    # Bound directory allocation before ZipFile constructs its object list.
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if eocd < 0 or eocd + 22 > len(data):
        raise ValueError("Missing ZIP directory")
    disk, start_disk, disk_count, count = struct.unpack_from("<HHHH", data, eocd + 4)
    if disk or start_disk or disk_count != count or not 0 < count <= 2048:
        raise ValueError("ZIP directory limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != count or len(set(names)) != len(names):
            raise ValueError("Duplicate ZIP entries")
        if sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
            raise ValueError("Package inflation limit")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or entry.flag_bits & 1 or entry.compress_type not in {0, 8}):
                raise ValueError("Unsafe ZIP entry")
        main_path, content_suffix, root_name = _OOXML[extension]

        def read_xml(name: str) -> ElementTree.Element:
            entry = archive.getinfo(name)
            if entry.file_size > 2 * 1024 * 1024:
                raise ValueError("XML limit")
            return _xml(archive.read(entry))

        content_types = read_xml("[Content_Types].xml")
        if content_types.tag != "{http://schemas.openxmlformats.org/package/2006/content-types}Types":
            raise ValueError("Invalid content types")
        expected_type = "application/vnd.openxmlformats-officedocument." + content_suffix
        matching = [node for node in content_types if node.get("PartName") == "/" + main_path]
        if len(matching) != 1 or matching[0].get("ContentType") != expected_type:
            raise ValueError("Office MIME mismatch")
        if any("macroenabled" in node.get("ContentType", "").lower()
               or "vba" in node.get("ContentType", "").lower() for node in content_types):
            raise ValueError("Macro-enabled OOXML is unsupported")
        relations = read_xml("_rels/.rels")
        office_relations = [node for node in relations
                            if node.get("Type", "").endswith("/officeDocument")]
        if (len(office_relations) != 1
                or office_relations[0].get("Target", "").lstrip("/") != main_path
                or office_relations[0].get("TargetMode") == "External"):
            raise ValueError("Office relationship mismatch")
        main = read_xml(main_path)
        namespaces = {
            ".xlsx": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            ".docx": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            ".pptx": "http://schemas.openxmlformats.org/presentationml/2006/main",
        }
        if main.tag != "{" + namespaces[extension] + "}" + root_name:
            raise ValueError("Office root mismatch")


def _check_legacy_office(data: bytes, extension: str) -> None:
    import olefile  # type: ignore[import-untyped]

    if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("Not OLE")
    with olefile.OleFileIO(io.BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT) as ole:
        streams = {tuple(path) for path in ole.listdir(streams=True, storages=False)}
        expected = {".xls": {"Workbook", "Book"}, ".doc": {"WordDocument"},
                    ".ppt": {"PowerPoint Document"}}[extension]
        if not any((name,) in streams and ole.get_size(name) > 0 for name in expected):
            raise ValueError("Office stream mismatch")
        if ("EncryptedPackage",) in streams:
            raise ValueError("Encrypted OOXML is unsupported")


def _check_image(data: bytes, extension: str) -> None:
    from PIL import Image

    formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
               ".webp": "WEBP", ".gif": "GIF"}
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format != formats[extension]:
                raise ValueError("Image MIME mismatch")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            frames = getattr(image, "n_frames", 1)
            if frames > MAX_IMAGE_FRAMES:
                raise ValueError("Frame limit")
            total_pixels = 0
            for frame in range(frames):
                image.seek(frame)
                total_pixels += image.width * image.height
                if total_pixels > MAX_IMAGE_PIXELS:
                    raise ValueError("Pixel limit")
                image.load()


def _worker() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (VALIDATION_MEMORY_BYTES, VALIDATION_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    data = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
        raise ValueError("Size limit")
    extension = PurePosixPath(sys.argv[1]).suffix.lower()
    if extension in _OOXML:
        _check_ooxml(data, extension)
    elif extension in {".xls", ".doc", ".ppt"}:
        _check_legacy_office(data, extension)
    else:
        _check_image(data, extension)


if __name__ == "__main__":
    try:
        _worker()
    except Exception:
        sys.exit(1)
