"""Application-level deployment-readiness tests."""

import asyncio

import pytest
from app.application.readiness import (
    CheckReadinessService,
    ReadinessComponentStatus,
    ReadinessFailureCode,
    ReadinessProbeError,
    ReadinessResult,
    ReadinessSnapshot,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _SnapshotProbe:
    def __init__(self, snapshot: ReadinessSnapshot) -> None:
        self.snapshot = snapshot

    async def inspect(self) -> ReadinessSnapshot:
        return self.snapshot


class _ErrorProbe:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def inspect(self) -> ReadinessSnapshot:
        raise self.error


class _BlockingProbe:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def inspect(self) -> ReadinessSnapshot:
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()
        raise AssertionError("blocking readiness probe unexpectedly completed")


def _snapshot(
    *,
    expected: frozenset[str],
    current: frozenset[str],
) -> ReadinessSnapshot:
    return ReadinessSnapshot(expected_heads=expected, current_heads=current)


def test_readiness_rejects_non_positive_timeout() -> None:
    probe = _SnapshotProbe(
        _snapshot(expected=frozenset({"head"}), current=frozenset({"head"}))
    )

    with pytest.raises(ValueError, match="timeout must be positive"):
        CheckReadinessService(probe, timeout_seconds=0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("expected", "current"),
    [
        (frozenset({"head"}), frozenset({"head"})),
        (
            frozenset({"head_a", "head_b"}),
            frozenset({"head_b", "head_a"}),
        ),
    ],
)
async def test_readiness_accepts_exact_migration_head_set(
    expected: frozenset[str],
    current: frozenset[str],
) -> None:
    service = CheckReadinessService(_SnapshotProbe(_snapshot(expected=expected, current=current)))

    result = await service.execute()

    assert result == ReadinessResult(
        ready=True,
        database=ReadinessComponentStatus.OK,
        migrations=ReadinessComponentStatus.OK,
        reason=None,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "current",
    [frozenset({"old_head"}), frozenset()],
)
async def test_readiness_rejects_mismatch_and_empty_database_heads(
    current: frozenset[str],
) -> None:
    service = CheckReadinessService(
        _SnapshotProbe(_snapshot(expected=frozenset({"head"}), current=current))
    )

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.OK,
        migrations=ReadinessComponentStatus.FAILED,
        reason=ReadinessFailureCode.MIGRATION_HEADS_MISMATCH,
    )


@pytest.mark.anyio
async def test_readiness_classifies_database_failure_without_checking_migrations() -> None:
    service = CheckReadinessService(
        _ErrorProbe(ReadinessProbeError(ReadinessFailureCode.DATABASE_UNAVAILABLE))
    )

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.FAILED,
        migrations=ReadinessComponentStatus.NOT_CHECKED,
        reason=ReadinessFailureCode.DATABASE_UNAVAILABLE,
    )


@pytest.mark.anyio
async def test_readiness_classifies_migration_state_failure_after_database_ping() -> None:
    service = CheckReadinessService(
        _ErrorProbe(ReadinessProbeError(ReadinessFailureCode.MIGRATION_STATE_UNAVAILABLE))
    )

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.OK,
        migrations=ReadinessComponentStatus.FAILED,
        reason=ReadinessFailureCode.MIGRATION_STATE_UNAVAILABLE,
    )


@pytest.mark.anyio
async def test_readiness_fails_closed_for_invalid_typed_probe_error() -> None:
    service = CheckReadinessService(
        _ErrorProbe(ReadinessProbeError(ReadinessFailureCode.CHECK_FAILED))
    )

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.UNKNOWN,
        migrations=ReadinessComponentStatus.UNKNOWN,
        reason=ReadinessFailureCode.CHECK_FAILED,
    )


@pytest.mark.anyio
async def test_readiness_redacts_unexpected_probe_failure() -> None:
    service = CheckReadinessService(_ErrorProbe(ValueError("secret programming detail")))

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.UNKNOWN,
        migrations=ReadinessComponentStatus.UNKNOWN,
        reason=ReadinessFailureCode.CHECK_FAILED,
    )


@pytest.mark.anyio
async def test_readiness_bounds_probe_and_cancels_timed_out_work() -> None:
    probe = _BlockingProbe()
    service = CheckReadinessService(probe, timeout_seconds=0.001)

    result = await service.execute()

    assert result == ReadinessResult(
        ready=False,
        database=ReadinessComponentStatus.UNKNOWN,
        migrations=ReadinessComponentStatus.UNKNOWN,
        reason=ReadinessFailureCode.CHECK_TIMEOUT,
    )
    assert probe.started.is_set()
    assert probe.cancelled.is_set()


@pytest.mark.anyio
async def test_readiness_does_not_swallow_external_task_cancellation() -> None:
    probe = _BlockingProbe()
    service = CheckReadinessService(probe, timeout_seconds=60.0)
    task = asyncio.create_task(service.execute())
    await probe.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.cancelled.is_set()
