"""Disabled-by-default boundary for Oko Telegram-start attribution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

JsonObject = dict[str, Any]

_START_PATTERN = re.compile(
    r"^/start(?:@[A-Za-z0-9_]{5,32})?\s+oko21_([A-Za-z0-9_-]{16,128})$"
)


@dataclass(frozen=True)
class OkoTelegramStart:
    """Minimum attribution payload sent to Oko Control."""

    tracking_token: str
    telegram_user_id: str
    telegram_username: str | None


class OkoAttributionDisposition(StrEnum):
    """Redacted outcome safe for metrics and operational logs."""

    DISABLED = "disabled"
    IGNORED = "ignored"
    RECORDED = "recorded"


class OkoAttributionPort(Protocol):
    """Transport boundary implemented outside the application layer."""

    async def record_telegram_start(self, start: OkoTelegramStart) -> None:
        """Record one idempotent Telegram start in Oko Control."""


def parse_oko_telegram_start(update: JsonObject) -> OkoTelegramStart | None:
    """Extract an Oko start token without retaining message text."""

    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    sender = message.get("from")
    if not isinstance(text, str) or not isinstance(sender, dict):
        return None
    match = _START_PATTERN.fullmatch(text.strip())
    if match is None:
        return None

    user_id = sender.get("id")
    if not isinstance(user_id, (int, str)) or isinstance(user_id, bool):
        return None
    username = sender.get("username")
    return OkoTelegramStart(
        tracking_token=match.group(1),
        telegram_user_id=str(user_id),
        telegram_username=username if isinstance(username, str) and username else None,
    )


class OkoAttributionService:
    """Recognize Oko starts and notify Oko Control only when explicitly enabled."""

    def __init__(self, *, enabled: bool, port: OkoAttributionPort) -> None:
        self._enabled = enabled
        self._port = port

    async def handle(self, update: JsonObject) -> OkoAttributionDisposition:
        start = parse_oko_telegram_start(update)
        if start is None:
            return OkoAttributionDisposition.IGNORED
        if not self._enabled:
            return OkoAttributionDisposition.DISABLED
        await self._port.record_telegram_start(start)
        return OkoAttributionDisposition.RECORDED
