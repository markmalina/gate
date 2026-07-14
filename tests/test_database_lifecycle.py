"""Process-scoped database runtime tests."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import cast
from unittest.mock import AsyncMock

import pytest
from app.api.deps import get_db_session
from app.infrastructure import database as database_module
from app.infrastructure.database import (
    DatabaseRuntime,
    build_database_runtime,
    database_runtime_lifespan,
)
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_build_database_runtime_owns_one_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = cast(AsyncEngine, object())
    calls: list[tuple[str, bool]] = []

    def fake_create_async_engine(database_url: str, *, pool_pre_ping: bool) -> AsyncEngine:
        calls.append((database_url, pool_pre_ping))
        return engine

    monkeypatch.setattr(database_module, "create_async_engine", fake_create_async_engine)

    runtime = build_database_runtime("  postgresql+asyncpg://gate  ")

    assert runtime.engine is engine
    assert runtime.session_factory.kw["expire_on_commit"] is False
    assert calls == [("postgresql+asyncpg://gate", True)]


def test_build_database_runtime_rejects_missing_url() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        build_database_runtime("  ")


class _SessionContext(AbstractAsyncContextManager[AsyncSession]):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.closed = False

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_get_db_session_reuses_lifespan_factory() -> None:
    sessions = [cast(AsyncSession, object()), cast(AsyncSession, object())]
    contexts: list[_SessionContext] = []

    def factory() -> _SessionContext:
        context = _SessionContext(sessions[len(contexts)])
        contexts.append(context)
        return context

    runtime = DatabaseRuntime(
        engine=cast(AsyncEngine, object()),
        session_factory=cast(async_sessionmaker[AsyncSession], factory),
    )
    app = FastAPI()
    app.state.database_runtime = runtime
    request = Request({"type": "http", "app": app})

    yielded: list[AsyncSession] = []
    for _ in sessions:
        dependency: AsyncIterator[AsyncSession] = get_db_session(request)
        yielded.append(await anext(dependency))
        await dependency.aclose()

    assert yielded == sessions
    assert len(contexts) == 2
    assert all(context.closed for context in contexts)


@pytest.mark.anyio
async def test_database_runtime_lifespan_disposes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )
    builds: list[str] = []

    def fake_build(database_url: str) -> DatabaseRuntime:
        builds.append(database_url)
        return runtime

    monkeypatch.setattr(database_module, "build_database_runtime", fake_build)

    async with database_runtime_lifespan("postgresql+asyncpg://gate") as yielded:
        assert yielded is runtime

    assert builds == ["postgresql+asyncpg://gate"]
    dispose.assert_awaited_once()


@pytest.mark.anyio
async def test_database_runtime_lifespan_disposes_after_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    engine = cast(AsyncEngine, type("Engine", (), {"dispose": dispose})())
    runtime = DatabaseRuntime(
        engine=engine,
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
    )

    monkeypatch.setattr(database_module, "build_database_runtime", lambda _url: runtime)

    with pytest.raises(RuntimeError, match="application failure"):
        async with database_runtime_lifespan("postgresql+asyncpg://gate"):
            raise RuntimeError("application failure")

    dispose.assert_awaited_once()
