"""Redacted worker heartbeat and degradation tests."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.worker_health import (
    REPLY_SEQUENCE_WORKER,
    TELEGRAM_WORKER,
    WorkerHealthRegistry,
    WorkerRuntimeState,
)

NOW = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)


def _registry() -> WorkerHealthRegistry:
    registry = WorkerHealthRegistry(heartbeat_stale_after_seconds=30)
    registry.register(TELEGRAM_WORKER, enabled=True)
    registry.register(REPLY_SEQUENCE_WORKER, enabled=False)
    return registry


def test_enabled_worker_is_degraded_until_first_heartbeat() -> None:
    snapshot = _registry().snapshot(observed_at=NOW)

    assert snapshot.status == "degraded"
    assert snapshot.workers[0].name == REPLY_SEQUENCE_WORKER
    assert snapshot.workers[0].state is WorkerRuntimeState.DISABLED
    assert snapshot.workers[1].name == TELEGRAM_WORKER
    assert snapshot.workers[1].state is WorkerRuntimeState.STARTING


def test_all_workers_disabled_never_claims_aggregate_health() -> None:
    registry = WorkerHealthRegistry()
    registry.register(TELEGRAM_WORKER, enabled=False)
    registry.register(REPLY_SEQUENCE_WORKER, enabled=False)

    snapshot = registry.snapshot(observed_at=NOW)

    assert snapshot.status == "degraded"
    assert all(worker.state is WorkerRuntimeState.DISABLED for worker in snapshot.workers)


def test_success_makes_worker_healthy_and_failure_is_redacted() -> None:
    registry = _registry()
    registry.record_success(TELEGRAM_WORKER, observed_at=NOW)
    healthy = registry.snapshot(observed_at=NOW + timedelta(seconds=5))

    assert healthy.status == "healthy"
    assert healthy.workers[1].state is WorkerRuntimeState.HEALTHY
    assert healthy.workers[1].heartbeat_age_seconds == 5
    assert healthy.workers[1].last_failure_age_seconds is None

    registry.record_failure(
        TELEGRAM_WORKER,
        error_code="telegram_worker_iteration_failed",
        observed_at=NOW + timedelta(seconds=6),
    )
    degraded = registry.snapshot(observed_at=NOW + timedelta(seconds=7))

    assert degraded.status == "degraded"
    assert degraded.workers[1].state is WorkerRuntimeState.DEGRADED
    assert degraded.workers[1].consecutive_failures == 1
    assert degraded.workers[1].last_error_code == "telegram_worker_iteration_failed"
    assert degraded.workers[1].last_failure_age_seconds == 1


def test_next_success_clears_failure_and_stale_heartbeat_degrades() -> None:
    registry = _registry()
    registry.record_failure(
        TELEGRAM_WORKER,
        error_code="telegram_worker_iteration_failed",
        observed_at=NOW,
    )
    registry.record_success(
        TELEGRAM_WORKER,
        observed_at=NOW + timedelta(seconds=1),
    )

    recovered = registry.snapshot(observed_at=NOW + timedelta(seconds=2))
    assert recovered.status == "healthy"
    assert recovered.workers[1].consecutive_failures == 0
    assert recovered.workers[1].last_error_code == "telegram_worker_iteration_failed"
    assert recovered.workers[1].last_failure_age_seconds == 2

    stale = registry.snapshot(observed_at=NOW + timedelta(seconds=32))
    assert stale.status == "degraded"
    assert stale.workers[1].state is WorkerRuntimeState.DEGRADED
    assert stale.workers[1].last_error_code == "worker_heartbeat_stale"


def test_registry_rejects_duplicate_unregistered_and_disabled_writes() -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="unique"):
        registry.register(TELEGRAM_WORKER, enabled=True)
    with pytest.raises(ValueError, match="registered"):
        registry.record_success("missing", observed_at=NOW)
    with pytest.raises(ValueError, match="disabled"):
        registry.record_failure(
            REPLY_SEQUENCE_WORKER,
            error_code="must_not_record",
            observed_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        registry.record_success(
            TELEGRAM_WORKER,
            observed_at=datetime(2026, 7, 24, 6, 0),
        )


def test_unbounded_failure_detail_is_replaced_by_a_stable_code() -> None:
    registry = _registry()
    registry.record_failure(
        TELEGRAM_WORKER,
        error_code="Private SQL detail with spaces and recipient",
        observed_at=NOW,
    )

    snapshot = registry.snapshot(observed_at=NOW)

    assert snapshot.workers[1].last_error_code == "worker_iteration_failed"
