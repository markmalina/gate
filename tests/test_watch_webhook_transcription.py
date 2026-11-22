import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.models.watch_voice import WatchVoiceNote
from app.worker import watch_voice
from app.worker import watch_webhook_transcription as worker
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FIRST = "1ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
SECOND = "2ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
BACKLOG = "3ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
FOREIGN = "4ac6d623-d0a6-47f7-a358-935ebf2fd5ad"


def test_gate_transcribes_without_mark_and_only_releases_approved_notes(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(engine.sync_engine, "connect")
        def register_lock(connection, _):
            connection.create_function("pg_advisory_xact_lock", 1, lambda _: None)

        async with engine.begin() as conn:
            await conn.run_sync(WatchVoiceNote.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(UTC)
        cfg = Settings(
            GATE_WORKSPACE_ID="mark-personal",
            DROPBOX_CALL_ACCOUNT_ID="owner",
            GATE_WATCH_WEBHOOK_ENABLED=True,
            GATE_WATCH_WEBHOOK_ARCHIVE_ENABLED=True,
            GATE_WATCH_VOICE_DATA_GATE_APPROVED=True,
            OPENAI_API_KEY="synthetic",
            GATE_WATCH_WEBHOOK_TRANSCRIPTION_ADMISSION=(
                '{"mode":"next","after":"' + now.isoformat() + '"}'
            ),
            GATE_WATCH_VOICE_FILE_ID="id:unrelated-legacy-filter",
        )
        canary = worker.watch_transcription_admission(cfg)
        assert canary == worker.TranscriptionAdmission("next", now, ())
        assert worker.watch_transcription_admission(
            cfg.model_copy(update={"gate_watch_voice_enabled": True})
        ) is None

        async def archive(note_id, at, *, owner="owner", via_webhook=True):
            async with factory.begin() as session:
                session.add(WatchVoiceNote(
                    id=note_id, workspace_key="mark-personal", account_id=owner,
                    file_id=f"id:{note_id}", revision="abcd", source_path="/Alpha/Voice/test.m4a",
                    filename="test.m4a", media_type="audio/mp4", size_bytes=3, sha256="synthetic",
                    data=b"abc", duration_seconds=1, received_at=at, dropbox_modified_at=at,
                    webhook_archived=via_webhook,
                ))

        calls: list[str] = []

        async def speech(settings, factory, http, note):
            assert note.transcription_state == "processing"
            calls.append(note.id)
            async with factory.begin() as session:
                await session.execute(
                    update(WatchVoiceNote).where(WatchVoiceNote.id == note.id).values(
                        transcription_state="transcribed", transcript="synthetic transcript",
                        transcribed_at=now,
                    )
                )

        monkeypatch.delenv("GATE_WATCH_APP_PRIVATE_KEY", raising=False)
        monkeypatch.setattr(watch_voice, "transcribe_claimed_note", speech)
        monkeypatch.setattr(worker, "transcription_workload_identity", lambda _: object())

        try:
            await archive(BACKLOG, now - timedelta(seconds=20), via_webhook=False)
            await archive(FOREIGN, now, owner="foreign")
            await archive(FIRST, now + timedelta(seconds=1))
            await archive(SECOND, now + timedelta(seconds=2))
            assert not await worker.drain_watch_transcription(cfg, factory, None, canary)
            assert not await worker.drain_watch_transcription(cfg, factory, None, canary)
            assert calls == [FIRST]

            live = worker.TranscriptionAdmission(
                "live", now, ((f"id:{BACKLOG}", "abcd"), (f"id:{FOREIGN}", "abcd"))
            )
            assert not await worker.drain_watch_transcription(cfg, factory, None, live)
            assert not await worker.drain_watch_transcription(cfg, factory, None, live)
            assert calls == [FIRST, BACKLOG, SECOND]
            async with factory() as session:
                for note_id in (FIRST, SECOND, BACKLOG):
                    assert (await session.get(WatchVoiceNote, note_id)).transcript
                assert (await session.get(WatchVoiceNote, FOREIGN)).transcript is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_gate_transcription_waits_for_archive_event_after_recovery(monkeypatch):
    async def scenario():
        cfg = Settings(
            DROPBOX_CALL_ACCOUNT_ID="owner",
            GATE_WATCH_WEBHOOK_ENABLED=True,
            GATE_WATCH_WEBHOOK_ARCHIVE_ENABLED=True,
            GATE_WATCH_VOICE_DATA_GATE_APPROVED=True,
            GATE_WATCH_WEBHOOK_TRANSCRIPTION_ADMISSION=(
                '{"mode":"next","after":"2026-11-22T12:00:00Z"}'
            ),
            OPENAI_API_KEY="synthetic",
        )
        wake = asyncio.Event()
        passes = asyncio.Queue()

        async def drain(*args):
            passes.put_nowait(None)
            return False

        monkeypatch.setattr(worker, "drain_watch_transcription", drain)
        task = asyncio.create_task(worker.run_watch_transcription_loop(cfg, None, wake))
        try:
            await asyncio.wait_for(passes.get(), timeout=2)
            assert passes.empty()
            wake.set()
            await asyncio.wait_for(passes.get(), timeout=2)
            assert passes.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
