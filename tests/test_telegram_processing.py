"""Tests for durable Telegram receipt and retry application contracts."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    TelegramProcessingDisposition,
    TelegramProcessingFailure,
    TelegramReceiptAcceptance,
    TelegramReceiptError,
    TelegramRetryPolicy,
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
