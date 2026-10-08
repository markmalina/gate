"""Read one bounded reply snapshot from an exact, already scoped Telegram update."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from app.application.telegram_attachments import validated_telegram_receipt_payload

JsonObject = dict[str, Any]


def extract_scoped_telegram_reply(
    *payloads: JsonObject | None,
    update_id: int | None,
    event_type: str,
    telegram_message_id: int | None,
    chat_id: str,
    business_connection_id: str | None,
) -> JsonObject | None:
    """Project only the immediate same-chat reply; never fetch history or media."""

    if type(update_id) is not int:
        return None
    for payload in payloads:
        exact = validated_telegram_receipt_payload(
            payload,
            receipt_update_id=update_id,
            message_update_id=update_id,
            event_type=event_type,
            telegram_message_id=telegram_message_id,
            chat_id=chat_id,
            business_connection_id=business_connection_id,
        )
        if exact is None:
            continue
        message = exact[event_type]
        if not any(key in message for key in ("reply_to_message", "quote", "external_reply")):
            continue
        result: JsonObject = {
            "status": "unavailable",
            "reason_code": "source_unavailable",
            "provider_message_id": None,
            "author": None,
            "sent_at": None,
            "text": None,
            "caption": None,
            "attachments": [],
            "quote": None,
        }
        if "external_reply" in message:
            result["reason_code"] = "external_reply_not_projected"
            return result
        reply = message.get("reply_to_message")
        if not isinstance(reply, dict):
            result["quote"] = _quote(message.get("quote"))
            return result
        chat = reply.get("chat")
        nested_chat_id = chat.get("id") if isinstance(chat, dict) else None
        nested_connection = reply.get("business_connection_id")
        reply_id = reply.get("message_id")
        if (
            type(nested_chat_id) not in {int, str}
            or str(nested_chat_id) != chat_id
            or type(reply_id) is not int
            or reply_id < 1
            or (nested_connection is not None and nested_connection != business_connection_id)
        ):
            result["reason_code"] = "source_identity_mismatch"
            return result
        result.update(
            provider_message_id=str(reply_id),
            author=_author(reply),
            sent_at=_sent_at(reply.get("date")),
            text=_text(reply.get("text"), 4096),
            caption=_text(reply.get("caption"), 1024),
            attachments=_attachments(reply),
            quote=_quote(message.get("quote")),
        )
        if result["text"] is not None or result["caption"] is not None or result["attachments"]:
            result["status"] = "snapshot"
            result["reason_code"] = None
        return result
    return None


def format_telegram_reply(reply: JsonObject) -> list[str]:
    """Render the same reply evidence in MCP's human-readable content."""

    if reply["status"] == "unavailable":
        lines = [f"reply source unavailable: {reply['reason_code']}"]
    else:
        author = reply["author"] or "author unknown"
        lines = [f"reply to message {reply['provider_message_id']} by {author}"]
    if reply["sent_at"]:
        lines.append(f"reply source date: {reply['sent_at']}")
    for field in ("text", "caption"):
        if reply[field] is not None:
            lines.append(f"reply source {field}: {reply[field]}")
    for attachment in reply["attachments"]:
        lines.append(f"reply source attachment: {attachment['filename']} ({attachment['kind']})")
    if reply["quote"] is not None:
        lines.append(f"quoted excerpt: {reply['quote']['text']}")
    return lines


def _text(value: object, maximum: int) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= maximum else None


def _author(reply: JsonObject) -> str | None:
    sender = reply.get("from")
    if isinstance(sender, dict):
        parts = [_text(sender.get(key), 255) for key in ("first_name", "last_name")]
        name = " ".join(part for part in parts if part)
        if name:
            return name
    sender_chat = reply.get("sender_chat")
    return _text(sender_chat.get("title"), 255) if isinstance(sender_chat, dict) else None


def _sent_at(value: object) -> str | None:
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _quote(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text"), 4096)
    position = value.get("position")
    if text is None or type(position) is not int or position < 0:
        return None
    return {
        "text": text,
        "position": position,
        "is_manual": value.get("is_manual") is True,
    }


def _attachments(reply: JsonObject) -> list[JsonObject]:
    # An allowlist excludes file IDs, thumbnails, vCards and nested replies.
    for kind in ("document", "photo", "voice", "audio", "video", "animation", "video_note"):
        raw = reply.get(kind)
        if kind == "photo" and isinstance(raw, list):
            candidates = [item for item in raw if isinstance(item, dict)]
            raw = candidates[-1] if candidates else None
        if not isinstance(raw, dict):
            continue
        filename = _text(raw.get("file_name"), 1024)
        name = (
            PurePosixPath(filename.replace("\\", "/").replace("\x00", "")).name
            if filename else ""
        )
        size = raw.get("file_size")
        return [{
            "kind": kind,
            "filename": name[:255] or f"telegram-{kind}",
            "mime_type": _text(raw.get("mime_type"), 128),
            "size_bytes": size if type(size) is int and size > 0 else None,
        }]
    return []
