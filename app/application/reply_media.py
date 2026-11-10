"""Validate a complete image package before any persistence or external effect."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID

from PIL import __version__ as pillow_version

from app.application.reply_drafts import ReplyDraftError
from app.application.reply_media_decoder import (
    MAX_MEDIA_BYTES,
    ImageDecodeError,
    decode_image,
)
from app.application.telegram_formatting import FormattingError, compile_text
from app.core.mcp_security import Principal

MAX_MEDIA_PACKAGE_BYTES = 24 * 1024 * 1024
MAX_MEDIA_BASE64_LENGTH = ((MAX_MEDIA_BYTES + 2) // 3) * 4
MAX_MEDIA_ITEMS = 10
MAX_MEDIA_PACKAGE_DECODE_SECONDS = 30
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
        if sum(_decoded_source_size(item.content_base64) for item in command.items) > (
            MAX_MEDIA_PACKAGE_BYTES
        ):
            raise _error("Source package exceeds 24 MiB")
        validated_items: list[ValidatedMediaItem] = []
        output_total = 0
        try:
            async with asyncio.timeout(MAX_MEDIA_PACKAGE_DECODE_SECONDS):
                for source_item in command.items:
                    item = await _validate_image(source_item)
                    output_total += item.size_bytes
                    if output_total > MAX_MEDIA_PACKAGE_BYTES:
                        raise _error("Prepared package exceeds 24 MiB")
                    validated_items.append(item)
        except TimeoutError as exc:
            raise _error(
                "Image package validation exceeded its deadline", "media_validation_timeout"
            ) from exc
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


def _decoded_source_size(content: str) -> int:
    if not isinstance(content, str) or not 0 < len(content) <= MAX_MEDIA_BASE64_LENGTH:
        raise _error("Image exceeds 8 MiB")
    if len(content) % 4 or content.endswith("==="):
        raise _error("Invalid image encoding")
    padding = 2 if content.endswith("==") else int(content.endswith("="))
    size = len(content) // 4 * 3 - padding
    if not 0 < size <= MAX_MEDIA_BYTES:
        raise _error("Image size must be 1 byte–8 MiB")
    return size


async def _validate_image(item: ReplyMediaInput) -> ValidatedMediaItem:
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
    try:
        source = base64.b64decode(item.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _error("Invalid image encoding") from exc
    source_sha256 = hashlib.sha256(source).hexdigest()
    if not hmac.compare_digest(source_sha256, item.expected_sha256):
        raise _error("Image SHA-256 differs", "media_changed")
    if item.caption_format not in {"plain", "markdown"}:
        raise _error("Caption format must be plain or markdown")
    if not isinstance(item.caption, str) or len(item.caption) > MAX_MEDIA_CAPTION_SOURCE_LENGTH:
        raise _error("Caption source exceeds 4096 characters")
    try:
        caption = compile_text(item.caption, item.caption_format, max_length=1024, allow_empty=True)
    except FormattingError as exc:
        raise ReplyDraftError(code=exc.code, message=exc.message) from exc
    try:
        decoded = await decode_image(source, item.media_type)
    except ImageDecodeError as exc:
        raise _error(str(exc), exc.code) from exc
    data = decoded.data
    return ValidatedMediaItem(
        filename=filename,
        media_type=item.media_type,
        source_size_bytes=len(source),
        size_bytes=len(data),
        source_sha256=source_sha256,
        output_sha256=hashlib.sha256(data).hexdigest(),
        width=decoded.width,
        height=decoded.height,
        transform_version=MEDIA_TRANSFORM_VERSION,
        transform=(
            f"metadata_strip;orientation={decoded.orientation};"
            f"colorspace={decoded.colorspace};reencode"
        ),
        caption=caption.text,
        caption_format=item.caption_format,
        caption_formatting_json=caption.to_dict(),
        data=data,
    )
