"""Keep the Telegram worker from creating a new pool for every iteration."""

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.worker.telegram_processing as worker
from app.core.config import Settings


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSessions:
    def __init__(self):
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return FakeSession()


class FakeEngine:
    def __init__(self):
        self.disposed = 0

    async def dispose(self):
        self.disposed += 1


def configure(monkeypatch, *, fail=False):
    seen = []

    async def run_once(**kwargs):
        seen.append(kwargs["session"])
        if fail:
            raise RuntimeError("synthetic database error")
        return SimpleNamespace(disposition="idle")

    async def drain(_sessions):
        return None

    for name in (
        "SessionFactoryTelegramIngressIdentityReader",
        "SessionFactoryCanonicalIngressStore",
        "SessionFactoryTelegramBusinessTextParityObserver",
        "SessionFactoryTelegramBusinessTextInboxProjector",
        "SqlTelegramQueueMaintenance",
        "RecoverStaleTelegramClaimsService",
    ):
        monkeypatch.setattr(worker, name, lambda arg: arg)
    monkeypatch.setattr(worker, "run_telegram_worker_once", run_once)
    monkeypatch.setattr(worker, "_drain_beta_context", drain)
    return seen


def settings():
    return Settings(
        _env_file=None,
        GATE_TELEGRAM_WORKER_ENABLED=True,
        GATE_ALPHA_CANARY_ENABLED=False,
        GATE_ALPHA_MEMBERSHIP_REPLAY_LIMIT=0,
        GATE_BETA_CANARY_ENABLED=False,
        GATE_BECOME_CONNECTOR_ENABLED=False,
        GATE_BECOME_CANARY_ENABLED=False,
        GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED=False,
        GATE_TELEGRAM_VOICE_ARCHIVE_ENABLED=False,
        GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED=False,
        DATABASE_URL="postgresql+asyncpg://example.invalid/gate",
    )


def test_shared_sessions_are_reused_without_worker_creating_or_disposing_engine(monkeypatch):
    seen = configure(monkeypatch)
    sessions = FakeSessions()
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: pytest.fail("new pool"))

    async def scenario():
        await worker.run_telegram_worker_with_sessions(settings(), sessions)
        await worker.run_telegram_worker_with_sessions(settings(), sessions)

    asyncio.run(scenario())
    assert sessions.opened == len(seen) == 2


def test_shared_factory_survives_one_failed_iteration(monkeypatch):
    sessions = FakeSessions()
    configure(monkeypatch, fail=True)
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: pytest.fail("new pool"))
    with pytest.raises(RuntimeError, match="synthetic database error"):
        asyncio.run(worker.run_telegram_worker_with_sessions(settings(), sessions))
    seen = configure(monkeypatch)
    asyncio.run(worker.run_telegram_worker_with_sessions(settings(), sessions))
    assert sessions.opened == 2
    assert len(seen) == 1


def test_standalone_disposes_its_own_pool_after_success_and_failure(monkeypatch):
    engine = FakeEngine()
    sessions = FakeSessions()
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(worker, "async_sessionmaker", lambda *a, **k: sessions)
    configure(monkeypatch)
    asyncio.run(worker._run_from_settings(settings()))
    assert engine.disposed == 1

    configure(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="synthetic database error"):
        asyncio.run(worker._run_from_settings(settings()))
    assert engine.disposed == 2


def test_standalone_disposes_pool_on_cancellation(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(worker, "async_sessionmaker", lambda *a, **k: FakeSessions())
    configure(monkeypatch)

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "run_telegram_worker_once", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._run_from_settings(settings()))
    assert engine.disposed == 1


def test_shared_sessions_are_used_when_voice_archive_is_enabled(monkeypatch):
    seen = configure(monkeypatch)
    sessions = FakeSessions()
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: pytest.fail("new pool"))

    @asynccontextmanager
    async def http_client():
        yield object()

    monkeypatch.setattr(worker, "build_telegram_http_client", http_client)
    for name in (
        "TelegramVoiceDownloadConfig",
        "TelegramVoiceDownloader",
        "SessionFactoryTelegramVoiceArchiveStore",
        "ArchiveTelegramVoiceService",
    ):
        monkeypatch.setattr(worker, name, lambda *args, **kwargs: object())
    configured = settings().model_copy(update={"gate_telegram_voice_archive_enabled": True})
    asyncio.run(worker.run_telegram_worker_with_sessions(configured, sessions))
    assert sessions.opened == len(seen) == 1


def test_shared_pool_reconnects_after_server_ends_idle_connection(monkeypatch):
    if os.environ.get("GATE_ENV") != "ci" or not os.environ.get("DATABASE_URL"):
        pytest.skip("requires the isolated CI PostgreSQL service")
    database_url = os.environ["DATABASE_URL"]
    # Never run the backend termination against an arbitrary database.
    if "@localhost:5432/" not in database_url:
        pytest.skip("requires the isolated localhost CI PostgreSQL service")
    configure(monkeypatch)
    backend_pids = []

    async def run_once(**kwargs):
        session = kwargs["session"]
        backend_pids.append(await session.scalar(text("SELECT pg_backend_pid()")))
        return SimpleNamespace(disposition="idle")

    monkeypatch.setattr(worker, "run_telegram_worker_once", run_once)

    async def scenario():
        engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=1)
        admin = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            configured = settings().model_copy(update={"database_url": database_url})
            await worker.run_telegram_worker_with_sessions(configured, sessions)
            async with admin.begin() as connection:
                assert await connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pids[0]}
                )
            await worker.run_telegram_worker_with_sessions(configured, sessions)
            assert len(backend_pids) == 2
            assert backend_pids[0] != backend_pids[1]
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())
