"""Actual entry-point resource ownership, including ASGI worker borrowing."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["telegram", "pilot"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_standalone_entry_owns_one_runtime_and_cleans_up_after_context(
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    cancel: bool,
) -> None:
    from app.infrastructure import database as db_module
    from app.worker import become_pilot_maintenance as pilot
    from app.worker import telegram_processing as telegram

    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://synthetic",
        GATE_TELEGRAM_WORKER_ENABLED=True,
    )
    database = SimpleNamespace(engine=object(), session_factory=object())
    events: list[str] = []

    def build(url: str, **kwargs: object) -> object:
        assert url == settings.database_url and kwargs["settings"] is settings
        events.append("build")
        return database

    async def close(actual: object) -> None:
        assert actual is database
        events.append("close")

    @asynccontextmanager
    async def admitted(actual: object, **kwargs: object):
        assert actual is database
        assert kwargs == {"settings": settings, "wait_baseline": True}
        events.append("admission_enter")
        try:
            yield
        finally:
            events.append("admission_exit")

    async def operate(*args: object, **kwargs: object) -> dict[str, bool]:
        assert events == ["build", "admission_enter"]
        actual = args[1] if entry == "telegram" else kwargs["database"]
        assert actual is database
        events.append("operation")
        if cancel:
            raise asyncio.CancelledError
        return {"synthetic": True}

    monkeypatch.setattr(db_module, "build_database_runtime", build)
    monkeypatch.setattr(db_module, "close_database_runtime", close)
    if entry == "telegram":
        monkeypatch.setattr(telegram, "runtime_process_admission", admitted)
        monkeypatch.setattr(telegram, "run_telegram_worker_with_runtime", operate)
        call = telegram._run_from_settings(settings)
    else:
        monkeypatch.setattr(pilot, "get_settings", lambda: settings)
        monkeypatch.setattr(pilot, "runtime_process_admission", admitted)
        monkeypatch.setattr(pilot, "_run_with_runtime", operate)
        call = pilot._run(argparse.Namespace(command="status"))
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        assert await call == {"synthetic": True}
    assert events == ["build", "admission_enter", "operation", "admission_exit", "close"]


@pytest.mark.anyio
async def test_asgi_iterations_borrow_same_runtime_until_worker_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from fastapi import FastAPI

    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://synthetic",
        GATE_TELEGRAM_WORKER_ENABLED=True,
        GATE_REPLY_SEQUENCE_WORKER_ENABLED=False,
        GATE_DOCUMENT_CLEANUP_ENABLED=False,
        GATE_BETA_CONNECTOR_ENABLED=False,
        GATE_BECOME_CONNECTOR_ENABLED=False,
        GATE_DROPBOX_CALL_INTAKE_ENABLED=False,
    )
    database = SimpleNamespace(engine=object(), session_factory=object(), sequences=None)
    events: list[str] = []
    iterations_done = asyncio.Event()

    @asynccontextmanager
    async def database_lifespan(url: str):
        assert url == settings.database_url
        events.append("db_enter")
        try:
            yield database
        finally:
            events.append("db_exit")

    @asynccontextmanager
    async def admitted(actual: object, **kwargs: object):
        assert actual is database and kwargs == {"settings": settings}
        events.append("admission_enter")
        try:
            yield
        finally:
            events.append("admission_exit")

    async def iteration(actual_settings: object, actual_database: object) -> None:
        assert actual_settings is settings and actual_database is database
        assert events[0:2] == ["db_enter", "admission_enter"]
        events.append("iteration")

    async def worker(actual_settings: object, *, run_iteration: object, **kwargs: object) -> None:
        try:
            await run_iteration(actual_settings)
            await run_iteration(actual_settings)
            iterations_done.set()
            await asyncio.Event().wait()
        finally:
            events.append("worker_exit")

    async def reconcile(*args: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "database_runtime_lifespan", database_lifespan)
    monkeypatch.setattr(main, "runtime_process_admission", admitted)
    monkeypatch.setattr(main, "run_telegram_worker_with_runtime", iteration)
    monkeypatch.setattr(main, "run_telegram_worker_loop", worker)
    monkeypatch.setattr(main, "_reconcile_all_telegram_webhooks", reconcile)
    monkeypatch.setattr(main, "build_readiness_service", lambda engine: object())
    monkeypatch.setattr(main, "_alpha_transcription_configured", lambda settings: False)
    monkeypatch.setattr(
        main,
        "build_telegram_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: pytest.fail("no HTTP in lifecycle test"))
        ),
    )
    async with main.lifespan(FastAPI()):
        await asyncio.wait_for(iterations_done.wait(), timeout=1)
        assert events == ["db_enter", "admission_enter", "iteration", "iteration"]
    assert events == [
        "db_enter",
        "admission_enter",
        "iteration",
        "iteration",
        "worker_exit",
        "admission_exit",
        "db_exit",
    ]


@pytest.mark.anyio
async def test_borrowed_iteration_never_builds_or_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure import database as db_module
    from app.worker import telegram_processing as telegram

    settings = Settings(
        GATE_TELEGRAM_WORKER_ENABLED=True,
        GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED=False,
        GATE_TELEGRAM_VOICE_ARCHIVE_ENABLED=False,
        GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED=False,
        GATE_ALPHA_CANARY_ENABLED=False,
        GATE_BETA_CANARY_ENABLED=False,
        GATE_BECOME_CANARY_ENABLED=False,
    )
    session = AsyncMock()

    @asynccontextmanager
    async def sessions():
        yield session

    database = SimpleNamespace(session_factory=sessions)
    result = object()
    run_once = AsyncMock(return_value=result)
    monkeypatch.setattr(telegram, "run_telegram_worker_once", run_once)
    monkeypatch.setattr(telegram, "_drain_beta_context", AsyncMock())
    monkeypatch.setattr(db_module, "build_database_runtime", lambda *a, **kw: pytest.fail("build"))
    monkeypatch.setattr(
        db_module, "close_database_runtime", AsyncMock(side_effect=AssertionError("close"))
    )
    assert await telegram.run_telegram_worker_with_runtime(settings, database) is result
    assert await telegram.run_telegram_worker_with_runtime(settings, database) is result
    assert run_once.await_count == 2
    assert all(call.kwargs["session"] is session for call in run_once.await_args_list)
