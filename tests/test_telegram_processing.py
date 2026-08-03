"""Tests for durable Telegram receipt and retry application contracts."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    ProcessTelegramOutboxService,
    TelegramProcessingDisposition,
    TelegramProcessingFailure,
    TelegramProcessingPlan,
    TelegramProcessingWorkItem,
    TelegramReceiptAcceptance,
    TelegramReceiptError,
    TelegramRetryPolicy,
    TelegramUpdateDeferred,
    TelegramUpdateProcessingError,
    TelegramWorkerDisposition,
    canonical_telegram_payload_digest,
)


class InMemoryAtomicReceiptStore:
    def __init__(self) -> None:
        self.acceptances: dict[tuple[str, int], TelegramReceiptAcceptance] = {}
        self.create_count = 0

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        key = (command.bot_key, command.update_id)
        existing = self.acceptances.get(key)
        if existing is not None:
            return TelegramReceiptAcceptance(
                receipt_id=existing.receipt_id,
                outbox_event_id=existing.outbox_event_id,
                duplicate=True,
            )
        self.create_count += 1
        acceptance = TelegramReceiptAcceptance(
            receipt_id=f"receipt-{self.create_count}",
            outbox_event_id=f"outbox-{self.create_count}",
            duplicate=False,
        )
        self.acceptances[key] = acceptance
        return acceptance


def _command(update_id: int = 42) -> AcceptTelegramReceiptCommand:
    return AcceptTelegramReceiptCommand(
        bot_key="gate_m_bot",
        update_id=update_id,
        payload={"update_id": update_id, "business_message": {}},
        received_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )


def test_duplicate_acceptance_reuses_receipt_and_outbox_identity() -> None:
    store = InMemoryAtomicReceiptStore()
    service = AcceptTelegramReceiptService(store)

    first = asyncio.run(service.execute(_command()))
    duplicate = asyncio.run(service.execute(_command()))

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.receipt_id == first.receipt_id
    assert duplicate.outbox_event_id == first.outbox_event_id
    assert store.create_count == 1


def test_canonical_payload_digest_ignores_json_field_order_and_detects_mutation(
) -> None:
    original = {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "chat": {"id": -100, "type": "supergroup"},
        },
    }
    reordered = {
        "message": {
            "chat": {"type": "supergroup", "id": -100},
            "message_id": 7,
        },
        "update_id": 42,
    }
    mutated = {
        "message": {
            "chat": {"type": "supergroup", "id": -100},
            "message_id": 8,
        },
        "update_id": 42,
    }

    assert canonical_telegram_payload_digest(original) == (
        canonical_telegram_payload_digest(reordered)
    )
    assert canonical_telegram_payload_digest(original) != (
        canonical_telegram_payload_digest(mutated)
    )


@pytest.mark.parametrize(
    "command",
    [
        AcceptTelegramReceiptCommand(
            bot_key=" ",
            update_id=1,
            payload={"update_id": 1},
            received_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
        AcceptTelegramReceiptCommand(
            bot_key="gate_m_bot",
            update_id=-1,
            payload={"update_id": -1},
            received_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
        AcceptTelegramReceiptCommand(
            bot_key="gate_m_bot",
            update_id=1,
            payload={"update_id": 2},
            received_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
    ],
)
def test_invalid_receipt_envelope_is_rejected_before_store(
    command: AcceptTelegramReceiptCommand,
) -> None:
    store = InMemoryAtomicReceiptStore()
    service = AcceptTelegramReceiptService(store)

    with pytest.raises(TelegramReceiptError):
        asyncio.run(service.execute(command))

    assert store.create_count == 0


def test_retry_policy_uses_bounded_exponential_delay() -> None:
    policy = TelegramRetryPolicy(
        max_attempts=6,
        base_delay_seconds=5,
        max_delay_seconds=12,
    )
    failed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    first = policy.plan_failure(
        TelegramProcessingFailure(attempt=1, failed_at=failed_at, error_code="timeout")
    )
    third = policy.plan_failure(
        TelegramProcessingFailure(attempt=3, failed_at=failed_at, error_code="timeout")
    )

    assert first.disposition == TelegramProcessingDisposition.RETRY
    assert first.next_attempt_at == failed_at + timedelta(seconds=5)
    assert third.next_attempt_at == failed_at + timedelta(seconds=12)


def test_retry_policy_dead_letters_at_hard_attempt_limit() -> None:
    policy = TelegramRetryPolicy(max_attempts=5)
    failed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    plan = policy.plan_failure(
        TelegramProcessingFailure(
            attempt=5,
            failed_at=failed_at,
            error_code="poison_update",
        )
    )

    assert plan.disposition == TelegramProcessingDisposition.DEAD_LETTER
    assert plan.next_attempt_at is None
    assert plan.error_code == "poison_update"


def test_retry_policy_rejects_non_positive_attempt() -> None:
    with pytest.raises(ValueError, match="attempt must be positive"):
        TelegramRetryPolicy().plan_failure(
            TelegramProcessingFailure(
                attempt=0,
                failed_at=datetime(2026, 7, 13, tzinfo=UTC),
                error_code="timeout",
            )
        )


class InMemoryProcessingQueue:
    def __init__(self, item: TelegramProcessingWorkItem | None) -> None:
        self.item = item
        self.claimed_at: datetime | None = None
        self.processed_at: datetime | None = None
        self.failure: tuple[datetime, TelegramProcessingPlan] | None = None

    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        self.claimed_at = now
        return self.item

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        assert item == self.item
        self.processed_at = processed_at

    async def record_failure(
        self,
        item: TelegramProcessingWorkItem,
        *,
        failed_at: datetime,
        plan: TelegramProcessingPlan,
    ) -> None:
        assert item == self.item
        self.failure = (failed_at, plan)


class StubProcessor:
    def __init__(self, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.items: list[TelegramProcessingWorkItem] = []

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        self.items.append(item)
        if self.error_code is not None:
            raise TelegramUpdateProcessingError(self.error_code)


class DeferredProcessor:
    def __init__(self, retry_after_seconds: int = 61) -> None:
        self.retry_after_seconds = retry_after_seconds
        self.items: list[TelegramProcessingWorkItem] = []

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        self.items.append(item)
        raise TelegramUpdateDeferred(
            "alpha_rate_limited_deferred",
            retry_after_seconds=self.retry_after_seconds,
        )


def _work_item(*, attempt: int = 1) -> TelegramProcessingWorkItem:
    return TelegramProcessingWorkItem(
        receipt_id="receipt-1",
        outbox_event_id="outbox-1",
        bot_key="gate_m_bot",
        update_id=42,
        payload={"update_id": 42},
        received_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        attempt=attempt,
    )


def test_worker_returns_idle_without_processing() -> None:
    queue = InMemoryProcessingQueue(None)
    processor = StubProcessor()

    result = asyncio.run(
        ProcessTelegramOutboxService(queue=queue, processor=processor).run_once(
            now=datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
        )
    )

    assert result.disposition == TelegramWorkerDisposition.IDLE
    assert processor.items == []


def test_worker_marks_successful_item_processed() -> None:
    item = _work_item()
    queue = InMemoryProcessingQueue(item)
    processor = StubProcessor()
    now = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)

    result = asyncio.run(
        ProcessTelegramOutboxService(queue=queue, processor=processor).run_once(now=now)
    )

    assert result.disposition == TelegramWorkerDisposition.PROCESSED
    assert result.receipt_id == item.receipt_id
    assert queue.processed_at == now
    assert queue.failure is None


def test_worker_schedules_redacted_retry() -> None:
    item = _work_item(attempt=2)
    queue = InMemoryProcessingQueue(item)
    processor = StubProcessor(" timeout ")
    now = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)

    result = asyncio.run(
        ProcessTelegramOutboxService(queue=queue, processor=processor).run_once(now=now)
    )

    assert result.disposition == TelegramWorkerDisposition.RETRY
    assert result.error_code == "timeout"
    assert result.next_attempt_at == now + timedelta(seconds=10)
    assert queue.failure is not None
    assert queue.processed_at is None


def test_worker_defers_backpressure_without_attempt_limit() -> None:
    item = _work_item(attempt=999)
    queue = InMemoryProcessingQueue(item)
    processor = DeferredProcessor()
    now = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)

    result = asyncio.run(
        ProcessTelegramOutboxService(queue=queue, processor=processor).run_once(now=now)
    )

    assert result.disposition == TelegramWorkerDisposition.RETRY
    assert result.error_code == "alpha_rate_limited_deferred"
    assert result.next_attempt_at == now + timedelta(seconds=61)
    assert queue.failure is not None
    assert queue.failure[1].disposition == TelegramProcessingDisposition.RETRY
    assert queue.processed_at is None


def test_worker_dead_letters_at_hard_limit() -> None:
    item = _work_item(attempt=5)
    queue = InMemoryProcessingQueue(item)
    processor = StubProcessor("poison_update")
    now = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)

    result = asyncio.run(
        ProcessTelegramOutboxService(queue=queue, processor=processor).run_once(now=now)
    )

    assert result.disposition == TelegramWorkerDisposition.DEAD_LETTER
    assert result.next_attempt_at is None
    assert queue.failure is not None
