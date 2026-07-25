"""Application contracts for durable Telegram receipt and outbox processing."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

JsonObject = dict[str, Any]

ALPHA_GROUP_CAPACITY_ERROR_CODE = "alpha_canary_group_capacity_reached"
ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE = "alpha_canary_group_already_pinned"
ALPHA_GROUP_CAPACITY_ERROR_CODES = frozenset(
    {
        ALPHA_GROUP_CAPACITY_ERROR_CODE,
        ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE,
    }
)


def is_alpha_membership_update(payload: Mapping[str, Any], *, update_id: int) -> bool:
    """Accept only a structurally bounded group membership update for replay."""

    payload_update_id = payload.get("update_id")
    membership = payload.get("my_chat_member")
    if type(payload_update_id) is not int or payload_update_id != update_id:
        return False
    if not isinstance(membership, dict):
        return False
    chat = membership.get("chat")
    new_chat_member = membership.get("new_chat_member")
    if not isinstance(chat, dict) or not isinstance(new_chat_member, dict):
        return False
    if chat.get("type") not in {"group", "supergroup"}:
        return False
    if type(chat.get("id")) is not int:
        return False
    return isinstance(new_chat_member.get("status"), str)


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


@dataclass(frozen=True)
class TelegramProcessingWorkItem:
    """One exclusively claimed receipt/outbox pair ready for processing."""

    receipt_id: str
    outbox_event_id: str
    bot_key: str
    update_id: int
    payload: JsonObject
    received_at: datetime
    attempt: int


class TelegramUpdateDeferred(Exception):
    """Expected backpressure carrying a safe code and an exact retry delay."""

    def __init__(self, error_code: str, *, retry_after_seconds: int) -> None:
        if not 1 <= retry_after_seconds <= 86_400:
            raise ValueError("retry_after_seconds must be between 1 and 86400")
        self.error_code = error_code.strip()[:128] or "processing_deferred"
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.error_code)


class TelegramUpdateProcessingError(Exception):
    """Expected processing failure carrying only a bounded safe error code."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code.strip()[:128] or "processing_error"
        super().__init__(self.error_code)


class TelegramUpdateProcessor(Protocol):
    """Process one claimed Telegram update without owning queue state."""

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        """Complete application processing or raise a safe coded failure."""


class TelegramProcessingQueue(Protocol):
    """Own exclusive claims and durable lifecycle transitions."""

    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        """Claim at most one due item and increment its attempt exactly once."""

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        """Atomically mark both receipt and outbox as completed."""

    async def record_failure(
        self,
        item: TelegramProcessingWorkItem,
        *,
        failed_at: datetime,
        plan: TelegramProcessingPlan,
    ) -> None:
        """Atomically schedule retry or hard dead-letter both records."""


class TelegramWorkerDisposition(StrEnum):
    """Observable result of one bounded worker iteration."""

    IDLE = "idle"
    PROCESSED = "processed"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class TelegramWorkerResult:
    """Redacted worker result without message content or exception detail."""

    disposition: TelegramWorkerDisposition
    receipt_id: str | None = None
    outbox_event_id: str | None = None
    attempt: int | None = None
    error_code: str | None = None
    next_attempt_at: datetime | None = None


class ProcessTelegramOutboxService:
    """Run one bounded claim/process/transition iteration."""

    def __init__(
        self,
        *,
        queue: TelegramProcessingQueue,
        processor: TelegramUpdateProcessor,
        retry_policy: TelegramRetryPolicy | None = None,
    ) -> None:
        self._queue = queue
        self._processor = processor
        self._retry_policy = retry_policy or TelegramRetryPolicy()

    async def run_once(self, *, now: datetime) -> TelegramWorkerResult:
        item = await self._queue.claim_due(now=now)
        if item is None:
            return TelegramWorkerResult(disposition=TelegramWorkerDisposition.IDLE)

        try:
            await self._processor.process(item)
        except TelegramUpdateDeferred as exc:
            plan = TelegramProcessingPlan(
                disposition=TelegramProcessingDisposition.RETRY,
                next_attempt_at=now + timedelta(seconds=exc.retry_after_seconds),
                attempt=item.attempt,
                error_code=exc.error_code,
            )
            await self._queue.record_failure(
                item,
                failed_at=now,
                plan=plan,
            )
            return TelegramWorkerResult(
                disposition=TelegramWorkerDisposition.RETRY,
                receipt_id=item.receipt_id,
                outbox_event_id=item.outbox_event_id,
                attempt=item.attempt,
                error_code=plan.error_code,
                next_attempt_at=plan.next_attempt_at,
            )
        except TelegramUpdateProcessingError as exc:
            plan = self._retry_policy.plan_failure(
                TelegramProcessingFailure(
                    attempt=item.attempt,
                    failed_at=now,
                    error_code=exc.error_code,
                )
            )
            await self._queue.record_failure(
                item,
                failed_at=now,
                plan=plan,
            )
            disposition = (
                TelegramWorkerDisposition.RETRY
                if plan.disposition == TelegramProcessingDisposition.RETRY
                else TelegramWorkerDisposition.DEAD_LETTER
            )
            return TelegramWorkerResult(
                disposition=disposition,
                receipt_id=item.receipt_id,
                outbox_event_id=item.outbox_event_id,
                attempt=item.attempt,
                error_code=plan.error_code,
                next_attempt_at=plan.next_attempt_at,
            )

        await self._queue.mark_processed(item, processed_at=now)
        return TelegramWorkerResult(
            disposition=TelegramWorkerDisposition.PROCESSED,
            receipt_id=item.receipt_id,
            outbox_event_id=item.outbox_event_id,
            attempt=item.attempt,
        )
