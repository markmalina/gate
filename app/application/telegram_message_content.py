"""Shared Telegram message-content normalization."""

from collections.abc import Mapping
from typing import Any


def extract_telegram_message_text(message: Mapping[str, Any]) -> str | None:
    """Return non-empty Telegram text or media caption, preserving text precedence."""

    for field_name in ("text", "caption"):
        value = message.get(field_name)
        if isinstance(value, str) and value.strip():
            return value
    return None
