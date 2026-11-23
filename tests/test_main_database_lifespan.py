"""Application integration tests for process-scoped database ownership."""

import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app import main as main_module
from app.infrastructure import database as database_module
from app.infrastructure.database import DatabaseRuntime
from app.services.telegram import TelegramClient
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_application_registers_liveness_and_readiness_routes() -> None:
    paths = set(main_module.create_app().openapi()["paths"])

    assert {"/health", "/live", "/ready"} <= paths


@pytest.mark.anyio
@pytest.mark.parametrize("mark_updates_enabled", [True, False])
async def test_application_lifespan_installs_and_removes_database_runtime(
    monkeypatch: pytest.MonkeyPatch,
    mark_updates_enabled: bool,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )
    builds: list[str] = []
    readiness_service = object()
    readiness_engines: list[AsyncEngine] = []
    reconciliation_started = asyncio.Event()
    reconciliation_cancelled = asyncio.Event()
    reconciliation_http_clients: list[httpx.AsyncClient] = []
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                json={"ok": True, "result": {}},
            )
        )
    )

    def fake_build(database_url: str) -> DatabaseRuntime:
        builds.append(database_url)
        return runtime

    def fake_build_readiness(engine: AsyncEngine) -> object:
        readiness_engines.append(engine)
        return readiness_service

    async def fake_reconcile(
        _settings: object,
        reconciliation_http_client: httpx.AsyncClient,
    ) -> None:
        reconciliation_http_clients.append(reconciliation_http_client)
        reconciliation_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            reconciliation_cancelled.set()
            raise

    monkeypatch.setattr(database_module, "build_database_runtime", fake_build)
    monkeypatch.setattr(main_module, "build_readiness_service", fake_build_readiness)
    monkeypatch.setattr(main_module, "reconcile_telegram_webhook_with_retries", fake_reconcile)
    monkeypatch.setattr(main_module, "build_telegram_http_client", lambda: http_client)
    watch_webhook_loop = AsyncMock()
    watch_mark_loop = AsyncMock()
    watch_archive_loop = AsyncMock()
    watch_transcription_loop = AsyncMock()
    notice_cancelled = asyncio.Event()

    async def fake_notice_loop(*_args: object) -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            notice_cancelled.set()
            raise

    notice_loop = AsyncMock(side_effect=fake_notice_loop)
    monkeypatch.setattr(main_module, "run_watch_webhook_loop", watch_webhook_loop)
    monkeypatch.setattr(main_module, "run_watch_mark_loop", watch_mark_loop)
    monkeypatch.setattr(main_module, "run_watch_archive_loop", watch_archive_loop)
    monkeypatch.setattr(main_module, "run_watch_transcription_loop", watch_transcription_loop)
    monkeypatch.setattr(main_module, "run_automatic_publisher", notice_loop)
    prefix = "GATE_REPLY_STOP_NOTICE_"
    for name, value in {
        "ENABLED": "true" if mark_updates_enabled else "false",
        "AUTO_AFTER": "2026-11-23T10:00:00Z",
        "APP_ID": "5100", "APP_INSTALLATION_ID": "5200", "APP_SLUG": "synthetic-reply",
        "APP_PRIVATE_KEY": "synthetic", "ACCOUNT_IDS": str(uuid4()), "PROJECT_IDS": str(uuid4()),
    }.items():
        monkeypatch.setenv(prefix + name, value)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "database_url": "postgresql+asyncpg://gate",
                "telegram_bot_token": "secret-token",
                "gate_watch_webhook_enabled": True,
                "gate_watch_webhook_archive_enabled": True,
                "gate_watch_webhook_transcription_admission": SecretStr("synthetic"),
                "gate_watch_mark_updates_enabled": mark_updates_enabled,
                "gate_workspace_id": "mark-personal",
            },
        )(),
    )
    app = FastAPI()

    async with main_module.lifespan(app):
        await reconciliation_started.wait()
        assert app.state.database_runtime is runtime
        assert app.state.readiness_service is readiness_service
        assert isinstance(app.state.telegram_client, TelegramClient)
        assert not http_client.is_closed
        settings, factory, mark_wake = watch_mark_loop.call_args.args
        _, _, archive_wake, transcription_wake, update_wake = watch_archive_loop.call_args.args
        expected_update_wake = mark_wake if mark_updates_enabled else None
        assert update_wake is expected_update_wake
        watch_webhook_loop.assert_called_once_with(
            settings, factory, app.state.watch_wake_event, archive_wake, mark_wake,
        )
        watch_transcription_loop.assert_called_once_with(
            settings, factory, transcription_wake, mark_wake=expected_update_wake,
        )
        if mark_updates_enabled:
            notice_loop.assert_awaited_once()
            factory, config, since = notice_loop.await_args.args
            assert factory is runtime.session_factory
            assert config.scope.workspace_id == "mark-personal"
            assert since == datetime(2026, 11, 23, 10, tzinfo=UTC)
        else:
            notice_loop.assert_not_called()

    assert builds == ["postgresql+asyncpg://gate"]
    assert notice_cancelled.is_set() == mark_updates_enabled
    assert readiness_engines == [engine]
    assert reconciliation_http_clients == [http_client]
    assert reconciliation_cancelled.is_set()
    assert http_client.is_closed
    dispose.assert_awaited_once()
    assert not hasattr(app.state, "database_runtime")
    assert not hasattr(app.state, "readiness_service")
    assert not hasattr(app.state, "telegram_client")


@pytest.mark.anyio
async def test_application_lifespan_disposes_database_after_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )
    reconciliation_started = asyncio.Event()

    async def fake_reconcile(*_args: object) -> None:
        reconciliation_started.set()
        await asyncio.Future()

    monkeypatch.setattr(database_module, "build_database_runtime", lambda _url: runtime)
    monkeypatch.setattr(main_module, "build_readiness_service", lambda _engine: object())
    monkeypatch.setattr(main_module, "reconcile_telegram_webhook_with_retries", fake_reconcile)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "database_url": "postgresql+asyncpg://gate",
                "telegram_bot_token": "secret-token",
            },
        )(),
    )
    app = FastAPI()

    with pytest.raises(RuntimeError, match="application failure"):
        async with main_module.lifespan(app):
            await reconciliation_started.wait()
            raise RuntimeError("application failure")

    dispose.assert_awaited_once()
    assert not hasattr(app.state, "database_runtime")
    assert not hasattr(app.state, "readiness_service")


@pytest.mark.anyio
async def test_readiness_builder_failure_disposes_database_without_starting_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )
    reconciliation_called = False

    async def fake_reconcile(*_args: object) -> None:
        nonlocal reconciliation_called
        reconciliation_called = True

    def fail_readiness(_engine: AsyncEngine) -> object:
        raise RuntimeError("invalid migration repository")

    monkeypatch.setattr(database_module, "build_database_runtime", lambda _url: runtime)
    monkeypatch.setattr(main_module, "build_readiness_service", fail_readiness)
    monkeypatch.setattr(main_module, "reconcile_telegram_webhook_with_retries", fake_reconcile)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "database_url": "postgresql+asyncpg://gate",
                "telegram_bot_token": "secret-token",
            },
        )(),
    )
    app = FastAPI()

    with pytest.raises(RuntimeError, match="invalid migration repository"):
        async with main_module.lifespan(app):
            pass

    assert reconciliation_called is False
    dispose.assert_awaited_once()
    assert not hasattr(app.state, "database_runtime")
    assert not hasattr(app.state, "readiness_service")
