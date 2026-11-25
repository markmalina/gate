"""Keep the Telegram worker from creating a new pool for every iteration."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import app.worker.telegram_processing as worker
import app.worker.telegram_voice_wiring as voice_wiring
import pytest
from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.config import Settings
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import Base, TelegramReceipt, TelegramReceiptStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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


def test_managed_loop_reuses_its_own_pool_and_disposes_on_cancellation(monkeypatch):
    engine = FakeEngine()
    sessions = FakeSessions()
    engines = []
    seen = []

    def create_engine(*args, **kwargs):
        engines.append((args, kwargs))
        return engine

    async def run_once(_settings, *, sessions):
        seen.append(sessions)
        return SimpleNamespace(disposition="idle", error_code=None)

    async def run_loop(configured, *, run_iteration, health_reporter):
        await run_iteration(configured)
        await run_iteration(configured)
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "create_async_engine", create_engine)
    monkeypatch.setattr(worker, "async_sessionmaker", lambda *a, **k: sessions)
    monkeypatch.setattr(worker, "run_telegram_worker_with_sessions", run_once)
    monkeypatch.setattr(worker, "run_telegram_worker_loop", run_loop)

    async def scenario():
        await asyncio.wait_for(worker.run_telegram_worker_managed_loop(settings()), timeout=2)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    assert len(engines) == 1
    assert engines[0][1]["pool_pre_ping"] is True
    assert seen == [sessions, sessions]
    assert engine.disposed == 1


def test_managed_loop_retries_failed_pool_initialization(monkeypatch):
    engine = FakeEngine()
    attempts = []
    failures = []
    sleeps = []

    def create_engine(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic initialization failure")
        return engine

    async def run_loop(*_args, **_kwargs):
        return None

    async def sleep(delay):
        sleeps.append(delay)

    reporter = SimpleNamespace(record_failure=lambda *a, **k: failures.append((a, k)))
    monkeypatch.setattr(worker, "create_async_engine", create_engine)
    monkeypatch.setattr(worker, "async_sessionmaker", lambda *a, **k: FakeSessions())
    monkeypatch.setattr(worker, "run_telegram_worker_loop", run_loop)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)
    asyncio.run(worker.run_telegram_worker_managed_loop(settings(), health_reporter=reporter))
    assert len(attempts) == 2
    assert len(failures) == 1
    assert sleeps == [5.0]
    assert engine.disposed == 1


@pytest.mark.parametrize(
    ("archive_enabled", "transcription_enabled"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_voice_services_share_worker_resources(
    monkeypatch, archive_enabled, transcription_enabled
):
    configure(monkeypatch)
    sessions = FakeSessions()
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **k: pytest.fail("new pool"))
    client = object()
    lifecycle = []

    @asynccontextmanager
    async def http_client():
        lifecycle.append("client_open")
        yield client
        lifecycle.append("client_close")

    async def run_once(**kwargs):
        lifecycle.append("run")
        assert (kwargs.get("voice_archive") is not None) is archive_enabled
        assert (kwargs.get("voice_updates") is not None) is transcription_enabled
        assert (kwargs.get("stale_recovery") is not None) is (
            archive_enabled and transcription_enabled
        )
        return SimpleNamespace(disposition="idle")

    monkeypatch.setattr(worker, "build_telegram_http_client", http_client)
    monkeypatch.setattr(worker, "run_telegram_worker_once", run_once)
    downloader = Mock(return_value=object())
    archive = Mock(wraps=voice_wiring.ArchiveTelegramVoiceService)
    transcription = Mock(wraps=voice_wiring.TelegramVoiceTranscriptionService)
    provider = Mock(wraps=voice_wiring.OpenAITranscriptionClient)
    monkeypatch.setattr(voice_wiring, "TelegramVoiceDownloader", downloader)
    monkeypatch.setattr(voice_wiring, "ArchiveTelegramVoiceService", archive)
    monkeypatch.setattr(voice_wiring, "TelegramVoiceTranscriptionService", transcription)
    monkeypatch.setattr(voice_wiring, "OpenAITranscriptionClient", provider)

    configured = settings().model_copy(update={
        "telegram_bot_token": "synthetic",
        "gate_telegram_voice_archive_enabled": archive_enabled,
        "gate_telegram_voice_transcription_enabled": transcription_enabled,
    })
    asyncio.run(worker.run_telegram_worker_with_sessions(configured, sessions))

    assert sessions.opened == 1
    assert archive.call_count == int(archive_enabled)
    assert transcription.call_count == provider.call_count == int(transcription_enabled)
    if archive_enabled or transcription_enabled:
        assert lifecycle == ["client_open", "run", "client_close"]
        downloader.assert_called_once()
        assert downloader.call_args.kwargs["http"] is client
    else:
        assert lifecycle == ["run"]
        downloader.assert_not_called()
    if archive_enabled:
        assert archive.call_args.kwargs["downloader"] is downloader.return_value
    if transcription_enabled:
        assert transcription.call_args.kwargs["downloader"] is downloader.return_value
        assert provider.call_args.kwargs["http"] is client


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


def test_persistent_pool_processes_real_queue_and_recovers_idle_connection(monkeypatch):
    if os.environ.get("GATE_ENV") != "ci" or "@localhost:5432/" not in os.environ.get(
        "DATABASE_URL", ""
    ):
        pytest.skip("requires the isolated localhost CI PostgreSQL service")
    database_url = os.environ["DATABASE_URL"]
    seen = []

    class Processor:
        async def process(self, item):
            seen.append(item.update_id)

    monkeypatch.setattr(worker, "ShadowClassifyingTelegramProcessor", lambda **kwargs: Processor())

    async def scenario():
        schema = f"worker_pool_test_{uuid4().hex}"
        admin = create_async_engine(database_url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await SqlTelegramReceiptStore(session).create_if_absent(
                    AcceptTelegramReceiptCommand(
                        bot_key="gate_m_bot",
                        update_id=42,
                        payload={"update_id": 42},
                        received_at=datetime.now(UTC),
                    )
                )

            async def pid():
                async with sessions() as session:
                    return await session.scalar(text("SELECT pg_backend_pid()"))

            configured = settings().model_copy(update={"database_url": database_url})
            before = await pid()
            first = await worker.run_telegram_worker_with_sessions(configured, sessions)
            second = await worker.run_telegram_worker_with_sessions(configured, sessions)
            assert first.disposition == "processed"
            assert second.disposition == "idle"
            assert await pid() == before
            assert seen == [42]
            async with sessions() as session:
                receipt = (await session.execute(select(TelegramReceipt))).scalar_one()
                assert receipt.status == TelegramReceiptStatus.PROCESSED.value
            async with admin.begin() as connection:
                assert await connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": before}
                )
            recovered = await worker.run_telegram_worker_with_sessions(configured, sessions)
            assert recovered.disposition == "idle"
            assert await pid() != before
            assert seen == [42]
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(scenario())
