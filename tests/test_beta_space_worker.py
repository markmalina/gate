"""Fault-boundary proofs for the durable Beta Space worker contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from app.application.beta_space_work import (
    BetaSpaceWorkFailurePlan,
    BetaSpaceWorkItem,
)
from app.core.config import Settings
from app.worker.beta_space import (
    BetaSpacePermanentWorkError,
    BetaSpaceWorkerResult,
    run_beta_space_worker_once,
)

NOW = datetime(2026, 8, 9, tzinfo=UTC)


class _Queue:
    def __init__(self, item: BetaSpaceWorkItem | None) -> None:
        self.item = item
        self.completed = 0
        self.effect_started = 0
        self.unknown: str | None = None
        self.failure: BetaSpaceWorkFailurePlan | None = None

    async def claim_due(
        self, *, now: datetime, lease_seconds: int
    ) -> BetaSpaceWorkItem | None:
        assert now == NOW
        assert lease_seconds == 120
        return self.item

    async def complete(self, *, item: BetaSpaceWorkItem, now: datetime) -> None:
        assert item is self.item
        assert now == NOW
        self.completed += 1

    async def mark_effect_started(
        self, *, item: BetaSpaceWorkItem, now: datetime
    ) -> None:
        assert item is self.item
        assert now == NOW
        self.effect_started += 1

    async def mark_unknown(
        self, *, item: BetaSpaceWorkItem, now: datetime, error_code: str
    ) -> None:
        assert item is self.item
        assert now == NOW
        self.unknown = error_code

    async def fail(
        self,
        *,
        item: BetaSpaceWorkItem,
        now: datetime,
        plan: BetaSpaceWorkFailurePlan,
    ) -> None:
        assert item is self.item
        assert now == NOW
        self.failure = plan


class _Processor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def process(self, item: BetaSpaceWorkItem, *, now: datetime) -> str:
        self.calls += 1
        assert now == NOW
        if self.error is not None:
            raise self.error
        return item.kind


def _item(*, kind: str = "reply", attempt: int = 1) -> BetaSpaceWorkItem:
    return BetaSpaceWorkItem(
        work_id="00000000-0000-0000-0000-000000000001",
        receipt_id="00000000-0000-0000-0000-000000000002",
        kind=kind,
        attempt=attempt,
        max_attempts=5,
        lease_expires_at=NOW + timedelta(minutes=2),
    )


async def _run(queue: _Queue, processor: _Processor) -> BetaSpaceWorkerResult:
    return await run_beta_space_worker_once(
        settings=Settings(_env_file=None),
        sessions=cast(object, object()),
        processor=processor,
        queue=queue,
        now=NOW,
    )


@pytest.mark.anyio
async def test_idle_and_success_are_bounded_to_one_claim() -> None:
    idle_queue = _Queue(None)
    idle = await _run(idle_queue, _Processor())
    assert idle.disposition == "idle"

    queue = _Queue(_item())
    processor = _Processor()
    result = await _run(queue, processor)
    assert result.disposition == "completed"
    assert processor.calls == 1
    assert queue.completed == 1
    assert queue.failure is None


@pytest.mark.anyio
async def test_transient_failure_gets_bounded_backoff_then_dlq() -> None:
    retry_queue = _Queue(_item(attempt=1))
    retry = await _run(retry_queue, _Processor(RuntimeError("synthetic")))
    assert retry.disposition == "retry"
    assert retry_queue.failure is not None
    assert retry_queue.failure.next_attempt_at == NOW + timedelta(seconds=5)

    terminal_queue = _Queue(_item(attempt=5))
    terminal = await _run(
        terminal_queue,
        _Processor(RuntimeError("synthetic")),
    )
    assert terminal.disposition == "failed"
    assert terminal_queue.failure is not None
    assert terminal_queue.failure.next_attempt_at is None


@pytest.mark.anyio
async def test_callback_marks_effect_before_send_and_ambiguity_is_terminal() -> None:
    queue = _Queue(_item(kind="callback_delivery"))
    result = await _run(
        queue,
        _Processor(BetaSpacePermanentWorkError("beta_callback_delivery_unknown")),
    )
    assert result.disposition == "unknown"
    assert queue.effect_started == 1
    assert queue.unknown == "beta_callback_delivery_unknown"
    assert queue.completed == 0


@pytest.mark.anyio
async def test_fail_closed_validation_error_never_retries() -> None:
    queue = _Queue(_item(kind="candidate_consent"))
    result = await _run(
        queue,
        _Processor(BetaSpacePermanentWorkError("beta_callback_owner_not_exact")),
    )
    assert result.disposition == "failed"
    assert queue.failure is not None
    assert queue.failure.next_attempt_at is None
