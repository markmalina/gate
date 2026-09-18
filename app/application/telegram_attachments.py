"""Pure Telegram attachment discovery for lazy Gate MCP retrieval."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

JsonObject = dict[str, Any]

# Telegram's hosted Bot API documents a 20 MB download ceiling.
TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES = 20_000_000
_MESSAGE_KEYS = (
    "business_message",
    "edited_business_message",
    "message",
    "edited_message",
)
_SINGLE_MEDIA_FIELDS = (
    "document",
    "audio",
    "video",
    "animation",
    "video_note",
)


class TelegramAttachmentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TelegramAttachmentReference:
    kind: str
    file_id: str = field(repr=False)
    file_unique_id: str = field(repr=False)
    filename: str
    mime_type: str
    size_bytes: int | None

    def __post_init__(self) -> None:
        if self.kind not in {
            "document",
            "photo",
            "audio",
            "video",
            "animation",
            "video_note",
        }:
            raise TelegramAttachmentError("telegram_attachment_kind_invalid")
        _token(self.file_id, "telegram_attachment_file_id_invalid", 512)
        _token(
            self.file_unique_id,
            "telegram_attachment_unique_id_invalid",
            512,
        )
        _token(self.filename, "telegram_attachment_filename_invalid", 255)
        _token(self.mime_type, "telegram_attachment_mime_type_invalid", 128)
        if self.size_bytes is not None and self.size_bytes <= 0:
            raise TelegramAttachmentError("telegram_attachment_size_invalid")

    @property
    def download_available(self) -> bool:
        return (
            self.size_bytes is None
            or self.size_bytes <= TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
        )

    @property
    def download_state(self) -> str:
        if self.download_available:
            return "available_on_request"
        return "telegram_cloud_limit_exceeded"

    def public_payload(
        self,
        *,
        task_id: UUID,
        message_id: UUID,
        index: int,
    ) -> JsonObject:
        return {
            "index": index,
            "kind": self.kind,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "download_available": self.download_available,
            "download_state": self.download_state,
            "selector": f"{task_id}:attachment:{message_id}:{index}",
        }


def extract_telegram_attachments(
    *payloads: JsonObject | None,
) -> tuple[TelegramAttachmentReference, ...]:
    """Extract attachment references without downloading or exposing provider ids."""

    attachments: list[TelegramAttachmentReference] = []
    seen: set[str] = set()
    for payload in payloads:
        for message in _telegram_messages(payload):
            reference = _message_attachment(message)
            if reference is None or reference.file_unique_id in seen:
                continue
            seen.add(reference.file_unique_id)
            attachments.append(reference)
    return tuple(attachments)


def serialize_telegram_attachments(
    *payloads: JsonObject | None,
    task_id: UUID,
    message_id: UUID,
) -> list[JsonObject]:
    return [
        attachment.public_payload(
            task_id=task_id,
            message_id=message_id,
            index=index,
        )
        for index, attachment in enumerate(
            extract_telegram_attachments(*payloads)
        )
    ]


def validated_telegram_receipt_payload(
    payload: JsonObject | None,
    *,
    receipt_update_id: int,
    message_update_id: int | None,
    event_type: str,
    telegram_message_id: int | None,
    chat_id: str,
    business_connection_id: str | None,
) -> JsonObject | None:
    """Return one receipt payload only when it is the exact message source."""

    if (
        not isinstance(payload, dict)
        or type(receipt_update_id) is not int
        or type(message_update_id) is not int
        or receipt_update_id != message_update_id
        or type(payload.get("update_id")) is not int
        or payload.get("update_id") != message_update_id
        or event_type not in _MESSAGE_KEYS
        or type(telegram_message_id) is not int
    ):
        return None
    message = payload.get(event_type)
    if not isinstance(message, dict):
        return None
    if (
        type(message.get("message_id")) is not int
        or message.get("message_id") != telegram_message_id
    ):
        return None
    chat = message.get("chat")
    raw_chat_id = chat.get("id") if isinstance(chat, dict) else None
    if type(raw_chat_id) not in {int, str} or str(raw_chat_id) != chat_id:
        return None
    raw_connection_id = message.get("business_connection_id")
    if business_connection_id is None:
        if raw_connection_id is not None:
            return None
    elif raw_connection_id != business_connection_id:
        return None
    return payload


def _telegram_messages(payload: JsonObject | None) -> tuple[JsonObject, ...]:
    if not isinstance(payload, dict):
        return ()
    messages = [
        value
        for key in _MESSAGE_KEYS
        if isinstance((value := payload.get(key)), dict)
    ]
    result = payload.get("result")
    if isinstance(result, dict):
        if any(key in result for key in (*_SINGLE_MEDIA_FIELDS, "photo")):
            messages.append(result)
        else:
            messages.extend(
                value
                for key in _MESSAGE_KEYS
                if isinstance((value := result.get(key)), dict)
            )
    if any(key in payload for key in (*_SINGLE_MEDIA_FIELDS, "photo")):
        messages.append(payload)
    return tuple(messages)


def _message_attachment(
    message: JsonObject,
) -> TelegramAttachmentReference | None:
    for kind in _SINGLE_MEDIA_FIELDS:
        raw = message.get(kind)
        if isinstance(raw, dict):
            return _reference(kind, raw)
    photos = message.get("photo")
    if not isinstance(photos, list):
        return None
    candidates = [
        item
        for item in photos
        if isinstance(item, dict)
        and isinstance(item.get("file_id"), str)
        and isinstance(item.get("file_unique_id"), str)
    ]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda item: (
            _positive_int(item.get("file_size")) or 0,
            (_positive_int(item.get("width")) or 0)
            * (_positive_int(item.get("height")) or 0),
        ),
    )
    return _reference("photo", selected)


def _reference(
    kind: str,
    raw: JsonObject,
) -> TelegramAttachmentReference | None:
    file_id = raw.get("file_id")
    unique_id = raw.get("file_unique_id")
    if (
        not isinstance(file_id, str)
        or not file_id.strip()
        or not isinstance(unique_id, str)
        or not unique_id.strip()
    ):
        return None
    raw_name = raw.get("file_name")
    filename = (
        _safe_filename(raw_name)
        if isinstance(raw_name, str)
        else _fallback_filename(kind, unique_id)
    )
    raw_mime = raw.get("mime_type")
    mime_type = (
        raw_mime.strip().lower().split(";", maxsplit=1)[0]
        if isinstance(raw_mime, str) and raw_mime.strip()
        else _fallback_mime_type(kind, filename)
    )
    try:
        return TelegramAttachmentReference(
            kind=kind,
            file_id=file_id.strip(),
            file_unique_id=unique_id.strip(),
            filename=filename,
            mime_type=mime_type,
            size_bytes=_positive_int(raw.get("file_size")),
        )
    except TelegramAttachmentError:
        return None


def _safe_filename(value: str) -> str:
    normalized = value.replace("\\", "/").replace("\x00", "").strip()
    name = PurePosixPath(normalized).name
    if not name:
        return "telegram-attachment.bin"
    return name[:255]


def _fallback_filename(kind: str, unique_id: str) -> str:
    suffix = {
        "photo": ".jpg",
        "audio": ".audio",
        "video": ".mp4",
        "animation": ".mp4",
        "video_note": ".mp4",
    }.get(kind, ".bin")
    safe_ref = "".join(character for character in unique_id if character.isalnum())[:16]
    return f"telegram-{kind}-{safe_ref or 'attachment'}{suffix}"


def _fallback_mime_type(kind: str, filename: str) -> str:
    if kind == "photo":
        return "image/jpeg"
    guessed = mimetypes.guess_type(filename)[0]
    if guessed:
        return guessed.lower()
    return {
        "audio": "audio/octet-stream",
        "video": "video/mp4",
        "animation": "video/mp4",
        "video_note": "video/mp4",
    }.get(kind, "application/octet-stream")


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _token(value: str, code: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise TelegramAttachmentError(code)
