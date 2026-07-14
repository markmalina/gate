"""Application integration tests for process-scoped database ownership."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app import main as main_module
from app.infrastructure import database as database_module
from app.infrastructure.database import DatabaseRuntime


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_application_lifespan_installs_and_removes_database_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )
    builds: list[str] = []
    reconciliation_started = asyncio.Event()

    def fake_build(database_url: str) -> DatabaseRuntime:
        builds.append(database_url)
        return runtime

    async def fake_reconcile(*_args: object) -> None:
        reconciliation_started.set()
        await asyncio.Future()

    monkeypatch.setattr(database_module, "build_database_runtime", fake_build)
    monkeypatch.setattr(main_module, "reconcile_telegram_webhook_with_retries", fake_reconcile)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: type("Settings", (), {"database_url": "postgresql+asyncpg://gate"})(),
    )
    app = FastAPI()

    async with main_module.lifespan(app):
        await reconciliation_started.wait()
        assert app.state.database_runtime is runtime

    assert builds == ["postgresql+asyncpg://gate"]
    dispose.assert_awaited_once()
    assert not hasattr(app.state, "database_runtime")
