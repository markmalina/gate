"""Unit coverage for durable Telegram Business voice archiving."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.api.audio_processing_history import telegram_processing_history
from app.application.telegram_queue_maintenance import RecoverStaleTelegramClaimsCommand
from app.application.telegram_voice_archive import (
    ArchiveTelegramVoiceCommand,
    ArchiveTelegramVoiceService,
    PersistTelegramVoiceTranscriptCommand,
    TelegramVoiceArchiveDirection,
    TelegramVoiceArchiveRecord,
    TelegramVoiceArchiveResult,
    TelegramVoiceTranscriptPersistenceResult,
    TelegramVoiceTranscriptStatus,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceError,
    TelegramVoiceReference,
)
from app.core.config import get_settings
from app.infrastructure.telegram_queue_maintenance import SqlTelegramQueueMaintenance
from app.infrastructure.telegram_voice_archive import SessionFactoryTelegramVoiceArchiveStore
from app.models.domain import (
    Account,
    Project,
    TelegramOutboxEvent,
    TelegramReceipt,
    TelegramVoiceArchive,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FakeDownloader:
    def __init__(self) -> None:
        self.references: list[TelegramVoiceReference] = []

    async def download(self, voice: TelegramVoiceReference) -> DownloadedVoice:
        self.references.append(voice)
        return DownloadedVoice(
            data=b"original-ogg-opus",
            filename="voice.oga",
            media_type="audio/ogg",
        )


class RecordingStore:
    def __init__(self) -> None:
        self.records: list[TelegramVoiceArchiveRecord] = []
        self.transcripts: list[PersistTelegramVoiceTranscriptCommand] = []

    async def get_existing(
        self, command: ArchiveTelegramVoiceCommand,
    ) -> TelegramVoiceArchiveResult | None:
        return None

    async def persist(
        self,
        record: TelegramVoiceArchiveRecord,
    ) -> TelegramVoiceArchiveResult:
        self.records.append(record)
        return TelegramVoiceArchiveResult(
            archive_id="archive-1",
            duplicate=False,
            size_bytes=record.size_bytes,
            media_type=record.media_type,
        )

    async def persist_transcript(
        self,
        command: PersistTelegramVoiceTranscriptCommand,
    ) -> TelegramVoiceTranscriptPersistenceResult:
        self.transcripts.append(command)
        return TelegramVoiceTranscriptPersistenceResult(
            archive_id=command.archive_id,
            duplicate=False,
            status=command.status,
        )


def test_business_voice_is_downloaded_and_persisted_as_original_audio() -> None:
    downloader = FakeDownloader()
    store = RecordingStore()
    service = ArchiveTelegramVoiceService(
        downloader=downloader,
        store=store,
    )
    received_at = datetime(2026, 7, 25, 6, 30, tzinfo=UTC)

    result = asyncio.run(
        service.archive(
            ArchiveTelegramVoiceCommand(
                receipt_id="11111111-1111-1111-1111-111111111111",
                account_id="22222222-2222-2222-2222-222222222222",
                project_id="33333333-3333-3333-3333-333333333333",
                direction=TelegramVoiceArchiveDirection.INBOUND,
                update={
                    "update_id": 42,
                    "business_message": {
                        "message_id": 7,
                        "business_connection_id": "opaque-connection",
                        "chat": {"id": 99, "type": "private"},
                        "voice": {
                            "file_id": "opaque-file",
                            "file_unique_id": "opaque-unique",
                            "file_size": 1024,
                            "duration": 4,
                        },
                    },
                },
                received_at=received_at,
            )
        )
    )

    assert result.duplicate is False
    assert len(downloader.references) == 1
    assert len(store.records) == 1
    record = store.records[0]
    assert record.telegram_update_id == 42
    assert record.telegram_message_id == 7
    assert record.direction is TelegramVoiceArchiveDirection.INBOUND
    assert record.duration_seconds == 4
    assert record.data == b"original-ogg-opus"
    assert record.size_bytes == len(record.data)
    assert record.media_type == "audio/ogg"
    assert len(record.sha256) == 64
    assert record.received_at == received_at


def test_transcript_is_persisted_beside_original_archive() -> None:
    store = RecordingStore()
    service = ArchiveTelegramVoiceService(
        downloader=FakeDownloader(),
        store=store,
    )
    transcribed_at = datetime(2026, 7, 25, 7, 30, tzinfo=UTC)

    result = asyncio.run(
        service.persist_transcript(
            PersistTelegramVoiceTranscriptCommand(
                archive_id="11111111-1111-1111-1111-111111111111",
                status=TelegramVoiceTranscriptStatus.TRANSCRIBED,
                transcribed_at=transcribed_at,
                text="Прекрасное утро — прекрасная жизнь.",
                language="ru",
                model="gpt-4o-mini-transcribe",
                reason_code="telegram_voice_transcribed",
            )
        )
    )

    assert result.duplicate is False
    assert store.transcripts[0].text == (
        "Прекрасное утро — прекрасная жизнь."
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_archive_replay_and_processing_milestones() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL archive checks")
    schema = f"voice_history_{uuid4().hex}"
    admin = create_async_engine(database_url)
    engine = create_async_engine(database_url, connect_args={
        "server_settings": {"search_path": schema},
    })
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionFactoryTelegramVoiceArchiveStore(sessions)
    downloader = FakeDownloader()
    archiver = ArchiveTelegramVoiceService(downloader=downloader, store=store)
    now = datetime(2026, 11, 22, 19, tzinfo=UTC)
    receipt_id, account_id, project_id = uuid4(), uuid4(), uuid4()
    payload = {
        "update_id": 42,
        "business_message": {
            "message_id": 7, "business_connection_id": "synthetic-connection",
            "chat": {"id": 99},
            "voice": {"file_id": "synthetic-file", "file_unique_id": "synthetic-id", "duration": 4},
        },
    }
    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            for model in (
                Project, Account, TelegramReceipt, TelegramOutboxEvent, TelegramVoiceArchive,
            ):
                await db.run_sync(model.__table__.create)
        async with sessions.begin() as db:
            db.add(Project(id=project_id, alias="history", name="Test", adapter_type="test"))
            db.add(Account(
                id=account_id, alias="history", owner="test", account_type="telegram_business",
                linked_project_id=project_id,
            ))
            db.add(TelegramReceipt(
                id=receipt_id, bot_key="telegram_business", update_id=42, payload=payload,
                received_at=now, status="processing", processing_started_at=now,
            ))
            await db.flush()
            db.add(TelegramOutboxEvent(
                receipt_id=receipt_id, status="processing", available_at=now, locked_at=now,
            ))

        command = ArchiveTelegramVoiceCommand(
            receipt_id=str(receipt_id), account_id=str(account_id), project_id=str(project_id),
            direction=TelegramVoiceArchiveDirection.INBOUND, update=payload, received_at=now,
        )
        archived = await archiver.archive(command)
        replay = await archiver.archive(command)
        assert replay.duplicate and replay.archive_id == archived.archive_id
        assert replay.archived_audio == archived.archived_audio
        assert len(downloader.references) == 1
        assert archived.archived_audio is not None
        assert archived.archived_audio.data == b"original-ogg-opus"
        with pytest.raises(TelegramVoiceError, match="archive_replay_mismatch"):
            await archiver.archive(replace(command, account_id=str(uuid4())))
        assert len(downloader.references) == 1

        claims = [now + timedelta(seconds=10), now + timedelta(seconds=11)]
        starts = await asyncio.gather(*(
            store.mark_recognition_started(archived.archive_id, at) for at in claims
        ), return_exceptions=True)
        assert starts.count(None) == 1
        assert isinstance(next(start for start in starts if start is not None), TelegramVoiceError)
        start_time = claims[starts.index(None)]
        save = PersistTelegramVoiceTranscriptCommand(
            archive_id=archived.archive_id, status=TelegramVoiceTranscriptStatus.TRANSCRIBED,
            transcribed_at=start_time + timedelta(seconds=15), text="Synthetic transcript",
        )
        with pytest.raises(TelegramVoiceError, match="recognition_claim_mismatch"):
            await store.persist_transcript(save)

        # An abandoned archive gets a recorded stop without changing the outbox or making a call.
        observed = now + timedelta(hours=2)
        async with sessions() as db:
            recovery = await SqlTelegramQueueMaintenance(db).recover_stale(
                RecoverStaleTelegramClaimsCommand(
                    stale_before=observed - timedelta(minutes=5), recovered_at=observed,
                    max_attempts=5, limit=1, require_missing_voice_archive=True,
                ),
            )
        assert recovery.recovered == 0
        async with sessions() as db:
            row = await db.get(TelegramVoiceArchive, UUID(archived.archive_id))
            assert row is not None and row.transcription_status is None
            assert row.transcription_stopped_at == observed
            assert row.transcription_stop_reason == "telegram_voice_stale_transcription"
            assert telegram_processing_history(row)["recognition_seconds"] is None

        save = replace(save, recognition_started_at=start_time, transcribed_at=observed)
        assert not (await store.persist_transcript(save)).duplicate
        assert (await store.persist_transcript(
            replace(save, transcribed_at=save.transcribed_at + timedelta(seconds=1)),
        )).duplicate
        async with sessions() as db:
            row = await db.get(TelegramVoiceArchive, UUID(archived.archive_id))
            assert row is not None and row.transcript_text == save.text
            assert row.transcription_queued_at == now and row.text_saved_at == save.transcribed_at
            history = telegram_processing_history(row)
            assert history["queue_wait_seconds"] == (start_time - now).total_seconds()
            assert history["recognition_seconds"] == (observed - start_time).total_seconds()
            assert history["stopped_at"] is None and history["stop_reason"] is None
        assert (await archiver.archive(command)).transcript_text == save.text
        assert len(downloader.references) == 1
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
