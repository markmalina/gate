"""Telegram loop integration for redacted worker degradation evidence."""

import asyncio

from app.application.worker_health import TELEGRAM_WORKER, WorkerHealthRegistry
from app.core.config import Settings
from app.worker.telegram_processing import (
    TelegramWorkerRuntimeResult,
    run_telegram_worker_loop,
)


def test_handled_worker_failure_is_reported_as_degraded() -> None:
    registry = WorkerHealthRegistry()
    registry.register(TELEGRAM_WORKER, enabled=True)

    async def scenario() -> None:
        stop = asyncio.Event()

        async def iteration(_settings: Settings) -> TelegramWorkerRuntimeResult:
            stop.set()
            return TelegramWorkerRuntimeResult(
                enabled=True,
                disposition="retry",
                attempt=1,
                error_code="identity_snapshot_failed",
            )

        await run_telegram_worker_loop(
            Settings(_env_file=None, GATE_TELEGRAM_WORKER_ENABLED=True),
            stop_event=stop,
            run_iteration=iteration,
            health_reporter=registry,
        )

    asyncio.run(scenario())

    worker = registry.snapshot().workers[0]
    assert worker.state == "degraded"
    assert worker.consecutive_failures == 1
    assert worker.last_error_code == "identity_snapshot_failed"
