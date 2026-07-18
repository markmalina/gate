"""Payload-private parity contract for ordinary Telegram Business text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)


class TelegramBusinessTextParityError(RuntimeError):
    """Bounded parity failure that never includes message or provider data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextParityCommand:
    """Expected canonical facts for one legacy compatibility observation."""

    update_id: int
    account_alias: str
    direction: CanonicalMessageDirection
    route: WorkRoute
    provider_dialog_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    text_content: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.update_id < 0:
            raise ValueError("update_id must be non-negative")
        if not self.account_alias or self.account_alias != self.account_alias.strip():
            raise ValueError("account_alias must be a bounded token")
        if not self.provider_dialog_id or not self.business_connection_id:
            raise ValueError("provider identity is required")
        if not self.text_content.strip():
            raise ValueError("text_content is required")


@dataclass(frozen=True, slots=True)
class TelegramBusinessLegacyTextFact:
    """Minimized legacy fact loaded without exposing its private payload."""

    account_alias: str
    direction: str
    reply_task_count: int
    provider_dialog_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    text_content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextParityResult:
    """Content-free successful parity evidence."""

    disposition: str = "matched"
    account_matched: bool = True
    direction_matched: bool = True
    dialog_matched: bool = True
    text_matched: bool = True
    reply_task_matched: bool = True


class TelegramBusinessTextParityObserver(Protocol):
    """Read-only observation boundary; it must never create workflow effects."""

    async def observe(
        self,
        command: TelegramBusinessTextParityCommand,
    ) -> TelegramBusinessTextParityResult:
        """Require exact legacy/canonical parity for one update."""


def compare_telegram_business_text_parity(
    command: TelegramBusinessTextParityCommand,
    legacy: TelegramBusinessLegacyTextFact,
) -> TelegramBusinessTextParityResult:
    """Fail on the first bounded mismatch without returning private values."""

    if legacy.account_alias != command.account_alias:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_account_mismatch"
        )
    expected_direction = command.direction.value
    if legacy.direction != expected_direction:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_direction_mismatch"
        )
    if (
        legacy.provider_dialog_id != command.provider_dialog_id
        or legacy.business_connection_id != command.business_connection_id
    ):
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_dialog_mismatch"
        )
    if legacy.text_content != command.text_content:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_text_mismatch"
        )
    expected_reply_tasks = (
        1
        if command.direction is CanonicalMessageDirection.INBOUND
        and command.route is WorkRoute.HUMAN_REPLY_TASK
        else 0
    )
    if legacy.reply_task_count != expected_reply_tasks:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_reply_task_mismatch"
        )
    return TelegramBusinessTextParityResult()
