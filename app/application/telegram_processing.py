"""Application contracts for durable Telegram receipt and outbox processing."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


JsonObject = dict[str, Any]


class TelegramReceiptError(ValueError):
    """Reject an invalid receipt envelope before persistence."""


class TelegramProcessingDisposition(StrEnum):
    """Bounded outcomes after a worker processing failure."""

    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class AcceptTelegramReceiptCommand:
    """Validated Telegram envelope passed to an atomic receipt store."""

    bot_key: str
    update_id: int
    payload: JsonObject
    received_at: datetime


@dataclass(frozen=True)
class TelegramReceiptAcceptance:
    """Stable identity returned for both first acceptance and duplicates."""

    receipt_id: str
    outbox_event_id: str
    duplicate: bool


class TelegramReceiptStore(Protocol):
    """Atomically create one receipt and one outbox event per Telegram update."""

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        """Return the existing acceptance when the deduplication key already exists."""


class AcceptTelegramReceiptService:
    """Validate an envelope and delegate one atomic receipt/outbox acceptance."""

    def __init__(self, store: TelegramReceiptStore) -> None:
        self._store = store

    async def execute(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        if not command.bot_key.strip():
            raise TelegramReceiptError("bot_key is required")
        if command.update_id < 0:
            raise TelegramReceiptError("update_id must be non-negative")
        if command.payload.get("update_id") != command.update_id:
            raise TelegramReceiptError("payload update_id does not match the envelope")
        return await self._store.create_if_absent(command)


@dataclass(frozen=True)
class TelegramProcessingFailure:
    """Redacted worker failure data used to plan retry or dead-letter."""

    attempt: int
    failed_at: datetime
    error_code: str


@dataclass(frozen=True)
class TelegramProcessingPlan:
    """Deterministic bounded action after a processing failure."""

    disposition: TelegramProcessingDisposition
    next_attempt_at: datetime | None
    attempt: int
    error_code: str


@dataclass(frozen=True)
class TelegramRetryPolicy:
    """Exponential retry policy with a hard attempt and delay cap."""

    max_attempts: int = 5
    base_delay_seconds: int = 5
    max_delay_seconds: int = 300

    def plan_failure(
        self,
        failure: TelegramProcessingFailure,
    ) -> TelegramProcessingPlan:
        if failure.attempt < 1:
            raise ValueError("attempt must be positive")
        error_code = failure.error_code.strip() or "unknown"
        if failure.attempt >= self.max_attempts:
            return TelegramProcessingPlan(
                disposition=TelegramProcessingDisposition.DEAD_LETTER,
                next_attempt_at=None,
                attempt=failure.attempt,
                error_code=error_code,
            )

        delay_seconds = min(
            self.base_delay_seconds * (2 ** (failure.attempt - 1)),
            self.max_delay_seconds,
        )
        return TelegramProcessingPlan(
            disposition=TelegramProcessingDisposition.RETRY,
            next_attempt_at=failure.failed_at + timedelta(seconds=delay_seconds),
            attempt=failure.attempt,
            error_code=error_code,
        )
