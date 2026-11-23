import asyncio
from datetime import UTC, datetime, timedelta
from json import dumps

from app.core.config import Settings
from app.infrastructure.openai_transcription import (
    WATCH_BENCHMARK_MAX_SECONDS,
    WATCH_BENCHMARK_SHA256,
)
from app.models.watch_voice import WatchVoiceNote
from app.worker import watch_voice
from app.worker import watch_webhook_transcription as worker
from pydantic import SecretStr
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

        async def archive(
            note_id, at, *, owner="owner", via_webhook=True, filename="test.m4a",
            digest="synthetic", duration=1,
        ):
            async with factory.begin() as session:
                session.add(WatchVoiceNote(
                    id=note_id, workspace_key="mark-personal", account_id=owner,
                    file_id=f"id:{note_id}", revision="abcd", source_path="/Alpha/Voice/test.m4a",
                    filename=filename, media_type="audio/mp4", size_bytes=3, sha256=digest,
                    data=b"abc", duration_seconds=duration, received_at=at, dropbox_modified_at=at,
                    webhook_archived=via_webhook,
                ))

        calls: list[str] = []
        canary_calls: list[str] = []
        canary_limits: list[int] = []

        async def speech(settings, factory, http, note, *, canary_max_seconds=None, mark_wake=None):
            assert note.transcription_state == "processing"
            calls.append(note.id)
            if canary_max_seconds is not None:
                canary_calls.append(note.id)
                canary_limits.append(canary_max_seconds)
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

            filename = "gate-gpt-transcribe-canary-abcd1234abcd.m4a"
            digest = "b" * 64
            probe = "5ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
            repeat = "6ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
            unrelated = "7ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
            wrong_bytes = "8ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
            await archive(wrong_bytes, now + timedelta(seconds=2), filename=filename)
            await archive(probe, now + timedelta(seconds=3), filename=filename, digest=digest)
            await archive(repeat, now + timedelta(seconds=4), filename=filename, digest=digest)
            await archive(unrelated, now + timedelta(seconds=5))
            candidate = worker.watch_transcription_admission(cfg.model_copy(update={
                "gate_watch_webhook_transcription_admission": SecretStr(dumps({
                    "mode": "live", "after": now.isoformat(),
                })),
                "gate_watch_gpt_transcribe_canary": SecretStr(dumps({
                    "after": now.isoformat(), "filename": filename, "sha256": digest,
                })),
            }))
            assert candidate and candidate.model_canary
            assert not await worker.drain_watch_transcription(cfg, factory, None, candidate)
            assert not await worker.drain_watch_transcription(cfg, factory, None, candidate)
            assert not await worker.drain_watch_transcription(cfg, factory, None, live)
            assert calls[-2:] == [probe, unrelated]
            assert canary_calls == [probe]

            benchmark = "9ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
            benchmark_name = "gate-gpt-transcribe-canary-859a17b840c9.m4a"
            await archive(
                benchmark, now + timedelta(seconds=6), filename=benchmark_name,
                digest=WATCH_BENCHMARK_SHA256, duration=160,
            )
            benchmark_grant = worker.WatchModelCanary(now, benchmark_name, WATCH_BENCHMARK_SHA256)
            admission = worker.TranscriptionAdmission("live", now, (), benchmark_grant)
            assert not await worker.drain_watch_transcription(cfg, factory, None, admission)
            assert not await worker.drain_watch_transcription(cfg, factory, None, admission)
            assert canary_calls == [probe, benchmark]
            assert canary_limits == [30, WATCH_BENCHMARK_MAX_SECONDS]
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
