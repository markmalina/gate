"""Recovery is consumed once and stores text without replaying message delivery."""

import asyncio
import hashlib
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.application.telegram_voice_transcription import TelegramVoiceOutcome
from app.core.config import Settings
from app.infrastructure.owner_voice_recovery import EXACT_ARCHIVES, reconcile_exact_call_copies
from app.worker import dropbox_calls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_recovery_commits_grant_before_provider_and_never_requeues(monkeypatch):
    archive = SimpleNamespace(
        id=EXACT_ARCHIVES[0], transcription_recovery_generation=3,
        data=b"synthetic audio", size_bytes=15, duration_seconds=25,
        sha256=hashlib.sha256(b"synthetic audio").hexdigest(),
    )
    commands = []
    commits = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalar(self, statement):
            compiled = str(statement)
            assert "FOR UPDATE" in compiled
            assert "account_id" in compiled and "project_id" in compiled
            assert "transcript_text IS NULL" in compiled
            return archive if archive.transcription_recovery_generation == 3 else None

        async def commit(self):
            commits.append(archive.transcription_recovery_generation)

        async def refresh(self, _row):
            pass

        async def get(self, _model, _id, **_kwargs):
            return archive

    class Service:
        def __init__(self, **_kwargs):
            pass

        async def transcribe(self, command):
            assert commits == [None]
            commands.append(command)
            return SimpleNamespace(outcome=TelegramVoiceOutcome.TRANSCRIBED,
                alpha_input_text="synthetic recovered transcript",
                reason_code="telegram_voice_transcribed")

    async def legacy_claim(**_kwargs):
        return None

    async def forbidden_requeue(**_kwargs):
        raise AssertionError("archive recovery must not dispatch messages")

    monkeypatch.setattr(dropbox_calls, "_claim_authorized_telegram_voice", legacy_claim)
    monkeypatch.setattr(dropbox_calls, "_requeue_recovered_telegram_voice", forbidden_requeue)
    monkeypatch.setattr(dropbox_calls, "TelegramVoiceTranscriptionService", Service)
    kwargs = dict(settings=Settings(OPENAI_API_KEY="synthetic-key",
                                   GATE_WORKSPACE_ID="mark-personal"),
                  session_factory=Session, http=object())
    asyncio.run(dropbox_calls._recover_authorized_telegram_voices(**kwargs))
    asyncio.run(dropbox_calls._recover_authorized_telegram_voices(**kwargs))
    assert len(commands) == 1
    assert commands[0].reservation_key == f"telegram-voice-recovery-v3:{archive.id}"
    assert commands[0].period_key == datetime.now(UTC).strftime("%Y-%m")
    assert archive.transcription_status == "transcribed"
    assert archive.summary_status == "summarized"


@pytest.mark.anyio
async def test_postgres_exact_grants_preserve_finished_and_foreign_archives():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required for migration regression")
    path = Path(__file__).parents[1] / "alembic/versions/0106_owner_voice_recovery.py"
    spec = importlib.util.spec_from_file_location("owner_voice_recovery_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(
                    "CREATE TEMP TABLE phone_call_recordings (id UUID PRIMARY KEY, "
                    "workspace_key TEXT, project_id UUID, source_account_id TEXT, "
                    "transcription_status TEXT, transcription_reason_code TEXT, "
                    "transcript_text TEXT, transcript_language TEXT, transcription_model TEXT, "
                    "transcribed_at TIMESTAMPTZ, summary_status TEXT, summary_text TEXT, "
                    "summary_model TEXT, summary_reason_code TEXT, summarized_at TIMESTAMPTZ, "
                    "size_bytes INTEGER, data BYTEA, duration_seconds INTEGER, sha256 TEXT) "
                    "ON COMMIT DROP"
                ))
                await connection.execute(text(
                    "CREATE TEMP TABLE telegram_voice_archives (id UUID PRIMARY KEY, "
                    "account_id UUID, project_id UUID, transcription_status TEXT, "
                    "transcription_reason_code TEXT, transcript_text TEXT, summary_text TEXT, "
                    "size_bytes INTEGER, data BYTEA, duration_seconds INTEGER, sha256 TEXT, "
                    "transcription_recovery_generation INTEGER, "
                    "transcription_recovery_authorized_at TIMESTAMPTZ) ON COMMIT DROP"
                ))
                for index, archive_id in enumerate(migration._IDS):
                    await connection.execute(text(
                        "INSERT INTO telegram_voice_archives VALUES (CAST(:id AS uuid), "
                        "CAST(:account AS uuid), 'e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0', "
                        "'unknown', 'openai_transcription_response_unknown', :transcript, "
                        "NULL, 4, CAST('test' AS bytea), 10, repeat('a',64), NULL, NULL)"
                    ), {"id": archive_id,
                        "account": ("0d8e025f-323d-5e86-a44c-a620f690ae4f" if index != 3
                                    else "00000000-0000-0000-0000-000000000001"),
                        "transcript": "existing text" if index == 2 else None})

                def migrate(sync_connection, direction):
                    migration.op = Operations(MigrationContext.configure(sync_connection))
                    getattr(migration, direction)()

                await connection.run_sync(migrate, "upgrade")
                assert await connection.scalar(text(
                    "SELECT count(*) FROM telegram_voice_archives "
                    "WHERE transcription_recovery_generation=3"
                )) == 2
                await connection.execute(text(
                    "UPDATE telegram_voice_archives SET transcription_recovery_generation=NULL, "
                    "transcription_recovery_authorized_at=now(), "
                    "transcription_status='transcribed', transcript_text='recovered' "
                    "WHERE id=CAST(:id AS uuid)"
                ), {"id": migration._IDS[0]})
                await connection.run_sync(migrate, "downgrade")
                assert await connection.scalar(text(
                    "SELECT count(*) FROM telegram_voice_archives "
                    "WHERE transcription_recovery_generation=3"
                )) == 0
                assert await connection.scalar(text(
                    "SELECT count(*) FROM telegram_voice_archives WHERE transcript_text IS NOT NULL"
                )) == 2
                # Equal stored digests are insufficient: actual bytes must match.
                for index, call_id in enumerate((
                    "00000000-0000-0000-0000-000000000001",
                    "0db01180-ba79-4ba5-b253-dd2eaf86b9ab",
                    "b9da7c16-543a-417a-9ec8-a844b43c9efc",
                )):
                    await connection.execute(text(
                        "INSERT INTO phone_call_recordings "
                        "(id, workspace_key, project_id, source_account_id, sha256, "
                        "size_bytes, data, transcription_status, transcript_text, summary_status, "
                        "summary_text, transcribed_at) VALUES (CAST(:id AS uuid), "
                        "'mark-personal', 'e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0', "
                        "'synthetic', repeat('a',64), 4, :data, :status, :transcript, "
                        "'summarized', 'synthetic summary', now())"
                    ), {"id": call_id, "data": b"test" if index != 2 else b"nope",
                        "status": "transcribed" if index == 0 else "unknown",
                        "transcript": "synthetic full transcript" if index == 0 else None})
                await reconcile_exact_call_copies(
                    async_sessionmaker(connection, expire_on_commit=False),
                    workspace_key="mark-personal",
                )
                assert await connection.scalar(text(
                    "SELECT transcript_text FROM phone_call_recordings "
                    "WHERE id='0db01180-ba79-4ba5-b253-dd2eaf86b9ab'::uuid"
                )) == "synthetic full transcript"
                assert await connection.scalar(text(
                    "SELECT transcript_text FROM phone_call_recordings "
                    "WHERE id='b9da7c16-543a-417a-9ec8-a844b43c9efc'::uuid"
                )) is None
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
