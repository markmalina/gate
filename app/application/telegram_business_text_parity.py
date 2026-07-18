"""Payload-private parity contract for ordinary Telegram Business text."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
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


class TelegramBusinessTextParityState(StrEnum):
    """Content-free durable parity outcomes."""

    MATCHED = "matched"
    PENDING = "pending"
    MISMATCH = "mismatch"


TELEGRAM_BUSINESS_TEXT_PARITY_PENDING_CODE = (
    "telegram_business_text_parity_legacy_pending"
)
TELEGRAM_BUSINESS_TEXT_PARITY_MISMATCH_CODES = frozenset(
    {
        "telegram_business_text_parity_account_mismatch",
        "telegram_business_text_parity_direction_mismatch",
        "telegram_business_text_parity_dialog_mismatch",
        "telegram_business_text_parity_text_mismatch",
        "telegram_business_text_parity_reply_task_mismatch",
        "telegram_business_text_parity_legacy_cardinality",
        "telegram_business_text_parity_legacy_invalid",
    }
)


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextParityCommand:
    """Expected canonical facts for one legacy compatibility observation."""

    workspace_key: str
    account_id: str = field(repr=False)
    canonical_message_id: str = field(repr=False)
    update_id: int
    account_alias: str
    direction: CanonicalMessageDirection
    route: WorkRoute
    provider_dialog_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    text_content: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.workspace_key or self.workspace_key != self.workspace_key.strip():
            raise ValueError("workspace_key must be a bounded token")
        if len(self.workspace_key) > 128:
            raise ValueError("workspace_key must be a bounded token")
        for value, name in (
            (self.account_id, "account_id"),
            (self.canonical_message_id, "canonical_message_id"),
        ):
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be a UUID") from exc
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


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextParityStatus:
    """Workspace/account-scoped aggregate safe for owner status output."""

    state: str
    total_count: int
    matched_count: int
    pending_count: int
    mismatch_count: int
    required_account_count: int
    covered_account_count: int
    mismatch_codes: tuple[tuple[str, int], ...]
    cutover_ready: bool

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state,
            "total_count": self.total_count,
            "matched_count": self.matched_count,
            "pending_count": self.pending_count,
            "mismatch_count": self.mismatch_count,
            "required_account_count": self.required_account_count,
            "covered_account_count": self.covered_account_count,
            "mismatch_codes": [
                {"code": code, "count": count}
                for code, count in self.mismatch_codes
            ],
            "cutover_ready": self.cutover_ready,
            "private_content_exposed": False,
        }


class TelegramBusinessTextParityObserver(Protocol):
    """Observe legacy parity and persist only content-free evidence."""

    async def observe(
        self,
        command: TelegramBusinessTextParityCommand,
    ) -> TelegramBusinessTextParityResult:
        """Require exact legacy/canonical parity for one update."""


def classify_telegram_business_text_parity_error(
    code: str,
) -> TelegramBusinessTextParityState:
    """Map one bounded observer code to a durable content-free state."""

    if code == TELEGRAM_BUSINESS_TEXT_PARITY_PENDING_CODE:
        return TelegramBusinessTextParityState.PENDING
    if code in TELEGRAM_BUSINESS_TEXT_PARITY_MISMATCH_CODES:
        return TelegramBusinessTextParityState.MISMATCH
    raise ValueError("unsupported Telegram Business text parity code")


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
