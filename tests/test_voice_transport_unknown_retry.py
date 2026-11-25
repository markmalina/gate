from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.application.telegram_voice_transcription import TelegramVoiceOutcome
from app.core.config import Settings, get_settings
from app.models.domain import (
    Account,
    Project,
    TelegramOutboxEvent,
    TelegramReceipt,
    TelegramVoiceArchive,
)
from app.worker import telegram_voice_recovery
from app.worker.telegram_voice_recovery import (
    TelegramVoiceRecoveryError,
    _telegram_voice_recovery_reservation_key,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0062_voice_transport_unknown_retry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "voice_transport_unknown_retry",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_retry_scope_fails_closed() -> None:
    migration = _migration()

    migration._require_exact_scope(1)
    for candidate_count in (0, 2):
        with pytest.raises(
            RuntimeError,
            match="voice_transport_unknown_retry_scope_mismatch",
        ):
            migration._require_exact_scope(candidate_count)

    assert migration.down_revision == "0062_mayak_oko_v2"
    assert migration._TRANSPORT_UNKNOWN == (
        "openai_transcription_transport_unknown"
    )
    assert migration._RECOVERY_GENERATION == 2


def test_exact_retry_is_isolated_atomic_content_free_and_reversible() -> None:
    root = Path(__file__).parents[1]
    migration = (
        root
        / "alembic"
        / "versions"
        / "0062_voice_transport_unknown_retry.py"
    ).read_text(encoding="utf-8")
    worker = (root / "app" / "worker" / "telegram_voice_recovery.py").read_text(
        encoding="utf-8"
    )
    model = (root / "app" / "models" / "domain.py").read_text(
        encoding="utf-8"
    )

    assert "WITH eligible AS MATERIALIZED" in migration
    assert "scope.candidate_count = 1" in migration
    assert "octet_length(a.data) > 0" in migration
    assert "a.transcript_text IS NULL" in migration
    assert "a.summary_text IS NULL" in migration
    assert "transcription_recovery_generation = :recovery_generation" in migration
    assert 'op.add_column(' in migration
    assert 'op.drop_column(' in migration
    assert "transcription_recovery_generation" in model
    assert ".limit(2)" in worker
    assert "telegram_voice_recovery_scope_ambiguous" in worker
    assert "archive.transcription_recovery_generation = None" in worker
    assert "telegram-voice-recovery-v{generation}:" in worker
    assert "telegram-voice-recovery-v1:" not in worker


def test_second_retry_uses_one_stable_isolated_reservation_generation() -> None:
    archive_id = uuid4()

    first = _telegram_voice_recovery_reservation_key(archive_id, 2)
    replay = _telegram_voice_recovery_reservation_key(archive_id, 2)

    assert first == replay
    assert first == f"telegram-voice-recovery-v2:{archive_id}"
    assert len(first) <= 128

    for invalid_generation in (1, 3):
        with pytest.raises(
            TelegramVoiceRecoveryError,
            match="telegram_voice_recovery_generation_invalid",
        ):
            _telegram_voice_recovery_reservation_key(
                archive_id,
                invalid_generation,
            )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome", [TelegramVoiceOutcome.TRANSCRIBED, TelegramVoiceOutcome.UNKNOWN],
)
async def test_legacy_grant_is_consumed_once_and_only_success_requeues(
    outcome: TelegramVoiceOutcome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL required to exercise PostgreSQL recovery locking")
    schema = f"voice_recovery_{uuid4().hex}"
    admin = create_async_engine(database_url)
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    account_id, project_id, receipt_id, archive_id, event_id = (uuid4() for _ in range(5))
    now = datetime.now(UTC)
    audio = b"synthetic audio"
    expected_error = "openai_transcription_transport_unknown"
    commands = []
    recovered = outcome is TelegramVoiceOutcome.TRANSCRIBED

    class Service:
        def __init__(self, **kwargs):
            self.downloader = kwargs["downloader"]

        async def transcribe(self, command):
            commands.append(command)
            async with sessions() as session:
                claimed = await session.get(TelegramVoiceArchive, archive_id)
                assert claimed.transcription_recovery_generation is None
                assert claimed.recognition_started_at is None
            assert (await self.downloader.download(command.voice)).data == audio
            await command.on_recognition_start(now)
            await asyncio.wait_for(recover(), timeout=5)
            return SimpleNamespace(
                outcome=outcome,
                alpha_input_text="synthetic transcript" if recovered else None,
                reason_code="telegram_voice_transcribed" if recovered else expected_error,
            )

    monkeypatch.setattr(telegram_voice_recovery, "TelegramVoiceTranscriptionService", Service)

    def reject_http(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("a recovery test must not make a network request")

    async def recover():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reject_http)) as http:
            await telegram_voice_recovery.recover_authorized_telegram_voice(
                settings=Settings(GATE_WORKSPACE_ID="mark-personal", OPENAI_API_KEY="synthetic"),
                session_factory=sessions,
                http=http,
            )

    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            for model in (Project, Account, TelegramReceipt, TelegramOutboxEvent,
                          TelegramVoiceArchive):
                await connection.run_sync(model.__table__.create)
        async with sessions.begin() as session:
            session.add_all([
                Project(id=project_id, alias="voice", name="Voice test", adapter_type="test"),
                Account(id=account_id, alias="test", owner="test", account_type="personal"),
                TelegramReceipt(
                    id=receipt_id, bot_key="telegram_business", update_id=1, payload={},
                    status="dead_letter", last_error_code=expected_error, received_at=now,
                ),
            ])
            await session.flush()
            session.add_all([
                TelegramVoiceArchive(
                    id=archive_id, receipt_id=receipt_id, account_id=account_id,
                    project_id=project_id, direction="inbound", telegram_update_id=1,
                    telegram_message_id=2, chat_id="synthetic-chat",
                    business_connection_id="synthetic-connection", duration_seconds=5,
                    filename="synthetic.ogg", media_type="audio/ogg", size_bytes=len(audio),
                    sha256=hashlib.sha256(audio).hexdigest(), data=audio, received_at=now,
                    transcription_status="unknown", transcription_reason_code=expected_error,
                    transcription_recovery_generation=2,
                ),
                TelegramOutboxEvent(
                    id=event_id, receipt_id=receipt_id, status="dead_letter", attempt_count=3,
                    last_error_code=expected_error, available_at=now,
                ),
            ])
        await recover()
        await recover()
        assert len(commands) == 1
        assert commands[0].reservation_key == f"telegram-voice-recovery-v2:{archive_id}"
        assert commands[0].budget_scope_kind == "telegram_connector"
        assert commands[0].connector_ref == "telegram_business"
        async with sessions() as session:
            archive = await session.get(TelegramVoiceArchive, archive_id)
            receipt = await session.get(TelegramReceipt, receipt_id)
            event = await session.get(TelegramOutboxEvent, event_id)
            assert archive.transcription_status == outcome.value
            assert archive.transcription_recovery_generation is None
            assert archive.recognition_started_at == now
            assert (archive.text_saved_at is not None) == recovered
            assert receipt.status == ("received" if recovered else "dead_letter")
            assert event.status == ("pending" if recovered else "dead_letter")
            assert event.attempt_count == (0 if recovered else 3)
            assert event.last_error_code == (None if recovered else expected_error)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
