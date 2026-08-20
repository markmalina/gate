"""Default-off process lifecycle proof for AQUB photo byte archival."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app import main
from app.core.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_both_flags_off_lifespan_constructs_no_archive_or_purge_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        GATE_AQUB_PHOTO_ARCHIVE_ENABLED=False,
    )
    session_factory = object()
    database_runtime = SimpleNamespace(
        engine=object(),
        session_factory=session_factory,
    )
    shared = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        follow_redirects=False,
        trust_env=False,
    )
    archive_worker = AsyncMock()
    purge_worker = AsyncMock()

    @asynccontextmanager
    async def database_lifespan(_database_url: str):
        yield database_runtime

    @asynccontextmanager
    async def http_lifespan():
        async with shared:
            yield shared

    async def reconciliation(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "database_runtime_lifespan", database_lifespan)
    monkeypatch.setattr(main, "build_telegram_http_client", http_lifespan)
    monkeypatch.setattr(main, "build_readiness_service", lambda _engine: object())
    monkeypatch.setattr(main, "_reconcile_all_telegram_webhooks", reconciliation)
    monkeypatch.setattr(main, "run_aqub_photo_archive_worker_loop", archive_worker)
    monkeypatch.setattr(
        main,
        "run_aqub_photo_archive_purge_worker_loop",
        purge_worker,
    )

    app = SimpleNamespace(state=SimpleNamespace())
    async with main.lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0)
        archive_worker.assert_not_called()
        purge_worker.assert_not_called()
        assert app.state.http_client is shared

    assert shared.is_closed is True


@pytest.mark.anyio
async def test_purge_only_lifespan_constructs_no_archive_download_or_provider_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        GATE_AQUB_PHOTO_ARCHIVE_ENABLED=False,
        GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=True,
    )
    session_factory = object()
    database_runtime = SimpleNamespace(
        engine=object(),
        session_factory=session_factory,
    )
    shared = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        follow_redirects=False,
        trust_env=False,
    )
    archive_worker = AsyncMock()
    purge_started = asyncio.Event()

    @asynccontextmanager
    async def database_lifespan(_database_url: str):
        yield database_runtime

    @asynccontextmanager
    async def http_lifespan():
        async with shared:
            yield shared

    async def reconciliation(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    async def purge_worker(**kwargs: object) -> None:
        assert kwargs["session_factory"] is session_factory
        assert "http_client" not in kwargs
        assert "settings" not in kwargs
        purge_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "database_runtime_lifespan", database_lifespan)
    monkeypatch.setattr(main, "build_telegram_http_client", http_lifespan)
    monkeypatch.setattr(main, "build_readiness_service", lambda _engine: object())
    monkeypatch.setattr(main, "_reconcile_all_telegram_webhooks", reconciliation)
    monkeypatch.setattr(main, "run_aqub_photo_archive_worker_loop", archive_worker)
    monkeypatch.setattr(
        main,
        "run_aqub_photo_archive_purge_worker_loop",
        purge_worker,
    )

    app = SimpleNamespace(state=SimpleNamespace())
    async with main.lifespan(app):  # type: ignore[arg-type]
        await asyncio.wait_for(purge_started.wait(), timeout=1)
        archive_worker.assert_not_called()
        assert app.state.http_client is shared

    assert shared.is_closed is True
