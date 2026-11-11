"""A dialog fingerprint is a concurrency precondition, never delivery authority."""

import hashlib
import hmac
import json
import re
from typing import Any

SNAPSHOT_KEY = "dialog_snapshot"
SNAPSHOT_PATTERN = r"^dialog-v1:[0-9a-f]{64}$"
DIALOG_EVENT_KINDS = (
    "business_message", "edited_business_message", "deleted_business_messages",
    "message", "edited_message",
)


def snapshot_digest(state: dict[str, Any]) -> str:
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return "dialog-v1:" + hashlib.sha256(payload.encode()).hexdigest()


def snapshot_matches(expected: object, current: str) -> bool:
    return (
        isinstance(expected, str)
        and re.fullmatch(SNAPSHOT_PATTERN, expected) is not None
        and hmac.compare_digest(expected, current)
    )


def telegram_dialog_key(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Select changes before content classification, projection or transcription."""
    for kind in DIALOG_EVENT_KINDS:
        event = payload.get(kind)
        if not isinstance(event, dict):
            continue
        connection = event.get("business_connection_id")
        chat = event.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if isinstance(connection, str) and connection and type(chat_id) is int:
            return connection, str(chat_id)
    return None
