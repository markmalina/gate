import asyncio
from uuid import UUID

import pytest
from app.worker.reply_sequence_supervisor import ReplySequenceSupervisor


def test_slow_dispatch_does_not_block_completion_or_replacement() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=2)
        a_started, b_started, release_a, c_finished = (asyncio.Event() for _ in range(4))
        timeline: list[str] = []

        async def a() -> None:
            timeline.append("a_start")
            a_started.set()
            await release_a.wait()
            timeline.append("a_finish")

        async def b() -> None:
            await a_started.wait()
            timeline.append("b_start")
            b_started.set()
            timeline.append("b_finish")

        async def c() -> None:
            timeline.append("c_finish")
            c_finished.set()

        assert pool.try_start(UUID(int=1), a)
        assert pool.try_start(UUID(int=2), b)
        await b_started.wait()
        result = await pool.wait_for_completion(timeout=1)
        assert [(r.sequence_id.int, r.outcome) for r in result] == [(2, "finished")]
        assert pool.try_start(UUID(int=3), c)
        await c_finished.wait()
        assert not release_a.is_set()
        release_a.set()
        report = await pool.shutdown()
        assert not report.unresolved
        assert timeline.index("a_start") < timeline.index("b_finish")
        assert timeline.index("b_start") < timeline.index("a_finish")
        assert timeline.index("c_finish") < timeline.index("a_finish")

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_capacity_and_duplicate_refuse_factory_without_creating_waiters() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=2)
        release = asyncio.Event()
        factories = 0

        def factory():
            nonlocal factories
            factories += 1
            return release.wait()

        assert pool.try_start(UUID(int=1), factory)
        assert not pool.try_start(UUID(int=1), factory)
        assert pool.try_start(UUID(int=2), factory)
        for value in range(3, 1003):
            assert not pool.try_start(UUID(int=value), factory)
        assert factories == 2
        assert pool.available_capacity == 0
        release.set()
        report = await pool.shutdown()
        assert len(report.completed) == 2
        assert not report.unresolved
        assert not pool.try_start(UUID(int=3), factory)
        assert factories == 2

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_failure_and_cancellation_are_observed_without_provider_error_text() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=2)

        async def failure() -> None:
            raise RuntimeError("private-provider-payload")

        async def cancelled() -> None:
            raise asyncio.CancelledError

        assert pool.try_start(UUID(int=1), failure)
        assert pool.try_start(UUID(int=2), cancelled)
        report = await pool.shutdown()
        assert [(r.sequence_id.int, r.outcome) for r in report.completed] == [
            (1, "failed"),
            (2, "cancelled"),
        ]
        assert "private-provider-payload" not in repr(report)
        assert pool.collect_finished() == ()

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_failed_dispatch_does_not_cancel_its_active_neighbor() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=2)
        started, release, finished = (asyncio.Event() for _ in range(3))

        async def neighbor() -> None:
            started.set()
            await release.wait()
            finished.set()

        async def failure() -> None:
            await started.wait()
            raise RuntimeError("synthetic")

        assert pool.try_start(UUID(int=1), neighbor)
        assert pool.try_start(UUID(int=2), failure)
        result = await pool.wait_for_completion(timeout=1)
        assert [(r.sequence_id.int, r.outcome) for r in result] == [(2, "failed")]
        assert pool.available_capacity == 1
        assert not finished.is_set()
        release.set()
        await finished.wait()
        report = await pool.shutdown()
        assert report.completed[0].outcome == "finished"

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_shutdown_retains_ownership_when_cancellation_is_not_acknowledged() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=1)
        started, cancellation_seen, release = (asyncio.Event() for _ in range(3))

        async def uncooperative() -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await release.wait()

        assert pool.try_start(UUID(int=1), uncooperative)
        await started.wait()
        report = await pool.shutdown(grace_seconds=0, cancel_seconds=0)
        await cancellation_seen.wait()
        assert report.unresolved == (UUID(int=1),)
        assert pool.available_capacity == 0
        assert not pool.try_start(UUID(int=2), uncooperative)
        release.set()
        completed = await pool.wait_for_completion(timeout=1)
        assert [(r.sequence_id.int, r.outcome) for r in completed] == [(1, "finished")]
        assert pool.available_capacity == 0

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_shutdown_cancels_cooperative_work_after_grace() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=1)
        started = asyncio.Event()

        async def dispatch() -> None:
            started.set()
            await asyncio.Event().wait()

        assert pool.try_start(UUID(int=1), dispatch)
        await started.wait()
        report = await pool.shutdown(grace_seconds=0, cancel_seconds=1)
        assert not report.unresolved
        assert report.completed[0].outcome == "cancelled"

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_poll_deadline_does_not_cancel_or_resend_work() -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=1)
        release = asyncio.Event()
        assert pool.try_start(UUID(int=1), release.wait)
        assert await pool.wait_for_completion(timeout=0) == ()
        assert pool.available_capacity == 0
        release.set()
        assert len((await pool.shutdown()).completed) == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_factory_error_does_not_leak_capacity() -> None:
    pool = ReplySequenceSupervisor(max_inflight=1)

    def fail():
        raise RuntimeError("factory failed")

    with pytest.raises(RuntimeError, match="factory failed"):
        pool.try_start(UUID(int=1), fail)
    assert pool.available_capacity == 1


@pytest.mark.parametrize("limit", [0, -1, 9, True, 2.0])
def test_invalid_limit_rejected(limit) -> None:
    with pytest.raises(ValueError):
        ReplySequenceSupervisor(max_inflight=limit)


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_invalid_timeouts_rejected(timeout: float) -> None:
    async def scenario() -> None:
        pool = ReplySequenceSupervisor(max_inflight=1)
        with pytest.raises(ValueError):
            await pool.wait_for_completion(timeout=timeout)
        with pytest.raises(ValueError):
            await pool.shutdown(grace_seconds=timeout)
        assert pool.available_capacity == 1

    asyncio.run(scenario())
