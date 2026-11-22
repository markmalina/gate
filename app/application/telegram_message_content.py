"""Shared Telegram message-content normalization."""

from collections.abc import Mapping
from typing import Any

from app.application.telegram_attachments import validated_telegram_receipt_payload
from app.application.telegram_rich_readback import (
    TelegramRichReadbackError,
    project_telegram_rich_text,
)


def extract_telegram_message_text(message: Mapping[str, Any]) -> str | None:
    """Return non-empty Telegram text or media caption, preserving text precedence."""

    for field_name in ("text", "caption"):
        value = message.get(field_name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def read_telegram_message_text(
    message: Any, *payloads: dict[str, Any] | None,
) -> tuple[str | None, str]:
    """Read normalized text or reconstruct it from an exact stored update."""
    stored = message.text
    if isinstance(stored, str) and stored.strip():
        return stored, "available"

    status = "source_unavailable"
    update_id = getattr(message, "telegram_update_id", None)
    event_type = getattr(message, "event_type", "")
    if type(update_id) is not int:
        return None, status
    for payload in (*payloads, getattr(message, "payload", None)):
        exact = validated_telegram_receipt_payload(
            payload,
            receipt_update_id=update_id,
            message_update_id=update_id,
            event_type=event_type,
            telegram_message_id=getattr(message, "telegram_message_id", None),
            chat_id=message.chat_id,
            business_connection_id=getattr(message, "business_connection_id", None),
        )
        if exact is None:
            continue
        raw = exact[event_type]
        try:
            text = extract_telegram_message_text(raw)
            if text is None and "rich_message" in raw:
                text = project_telegram_rich_text(raw["rich_message"])
        except TelegramRichReadbackError:
            status = "processing_failed"
            continue
        if text is not None:
            return text, "available"
        if status != "processing_failed" and _is_confirmed_no_text(raw):
            status = "no_text"
    return None, status


def _is_confirmed_no_text(raw: dict[str, Any]) -> bool:
    return (
        "rich_message" in raw
        or any(isinstance(raw.get(field), str) for field in ("text", "caption"))
        or any(isinstance(raw.get(field), (dict, list)) for field in (
            "animation", "audio", "checklist", "contact", "dice", "document",
            "game", "live_photo", "location", "paid_media", "photo", "poll",
            "sticker", "story", "venue", "video", "video_note", "voice",
        ))
    )
