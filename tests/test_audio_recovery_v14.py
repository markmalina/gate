"""One-shot repair uses a new reservation while preserving historical results."""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.application.telegram_voice_transcription import TelegramVoiceOutcome
from app.core.config import Settings
from app.worker import dropbox_calls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("status,reason", [
    ("recovery_v14", "openai_transcription_rejected_http_400"),
    ("processing", "dropbox_call_processing_v14"),
])
def test_recovery_and_restart_keep_same_new_reservation(monkeypatch, status, reason):
    recording = SimpleNamespace(
        id=UUID("050409d7-4e44-4df6-896f-c5023ced70ae"),
        transcription_status=status, transcription_reason_code=reason,
        duration_seconds=2321, received_at=datetime(2026, 7, 26, tzinfo=UTC),
        source_file_id="synthetic-file", source_revision="synthetic-rev",
        size_bytes=37_585_783,
    )
    commands = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalar(self, statement):
            compiled = str(statement)
            assert "FOR UPDATE" in compiled
            return recording

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

        async def get(self, _model, _id):
            return recording

    class Service:
        def __init__(self, **_kwargs):
            pass

        async def transcribe(self, command):
            commands.append(command)
            return SimpleNamespace(
                outcome=TelegramVoiceOutcome.TRANSCRIBED,
                alpha_input_text="synthetic complete transcript",
                reason_code="telegram_voice_transcribed",
            )

    monkeypatch.setattr(dropbox_calls, "TelegramVoiceTranscriptionService", Service)
    now = datetime.now(UTC)
    asyncio.run(dropbox_calls._transcribe_recording(
        settings=Settings(OPENAI_API_KEY="synthetic-key"), session_factory=Session,
        http=object(), recording=recording,
    ))
    assert commands[0].reservation_key == f"dropbox-call-v14:{recording.id}"
    assert commands[0].period_key == now.strftime("%Y-%m")
    assert commands[0].occurred_at >= now
    assert recording.transcription_status == "transcribed"
    assert recording.summary_status == "summarized"


@pytest.mark.anyio
async def test_postgres_grants_only_exact_definite_rejections_and_preserves_work():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required for recovery migration regression")
    path = Path(__file__).parents[1] / "alembic/versions/0105_seekable_audio_recovery.py"
    spec = importlib.util.spec_from_file_location("audio_recovery_v14", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                statuses = ",".join(f"'{s}'" for s in migration._OLD_STATUSES)
                await connection.execute(text(
                    "CREATE TEMP TABLE phone_call_recordings (id UUID PRIMARY KEY, "
                    "workspace_key TEXT, transcription_status TEXT NOT NULL, "
                    "transcription_reason_code TEXT, transcript_text TEXT, summary_text TEXT, "
                    "size_bytes INTEGER, data BYTEA, duration_seconds INTEGER, sha256 TEXT, "
                    "CONSTRAINT ck_phone_call_recordings_transcription_status "
                    f"CHECK (transcription_status IN ({statuses}))) ON COMMIT DROP"
                ))
                for recording_id, size in migration._EXACT_RECORDINGS:
                    await connection.execute(text(
                        "INSERT INTO phone_call_recordings VALUES "
                        "(CAST(:id AS uuid), 'mark-personal', 'rejected', "
                        "'openai_transcription_rejected_http_400', NULL, NULL, :size, "
                        "CAST(repeat('x', :size) AS bytea), 1500, repeat('a',64))"
                    ), {"id": recording_id, "size": size})

                def migrate(sync_connection, direction):
                    migration.op = Operations(MigrationContext.configure(sync_connection))
                    getattr(migration, direction)()

                # Unknown work is not a recovery candidate, even with the exact ID.
                first_id = migration._EXACT_RECORDINGS[0][0]
                await connection.execute(text(
                    "UPDATE phone_call_recordings SET transcription_status='unknown' "
                    "WHERE id=CAST(:id AS uuid)"
                ), {"id": first_id})
                await connection.run_sync(migrate, "upgrade")
                rows = (await connection.execute(text(
                    "SELECT transcription_status FROM phone_call_recordings"
                ))).scalars().all()
                assert sorted(rows) == ["recovery_v14", "unknown"]
                # A consumed grant must survive downgrade, including its text.
                await connection.execute(text(
                    "UPDATE phone_call_recordings SET transcription_status='transcribed', "
                    "transcript_text='synthetic completed text' "
                    "WHERE transcription_status='recovery_v14'"
                ))
                await connection.run_sync(migrate, "downgrade")
                assert await connection.scalar(text(
                    "SELECT count(*) FROM phone_call_recordings "
                    "WHERE transcription_status='transcribed' AND transcript_text IS NOT NULL"
                )) == 1
                assert await connection.scalar(text(
                    "SELECT count(*) FROM phone_call_recordings "
                    "WHERE transcription_status='unknown'"
                )) == 1
                await connection.execute(text(
                    "UPDATE phone_call_recordings SET transcription_status='rejected' "
                    "WHERE id=CAST(:id AS uuid)"
                ), {"id": first_id})
                await connection.run_sync(migrate, "upgrade")
                await connection.run_sync(migrate, "downgrade")
                assert await connection.scalar(text(
                    "SELECT count(*) FROM phone_call_recordings "
                    "WHERE transcription_status='rejected'"
                )) == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
