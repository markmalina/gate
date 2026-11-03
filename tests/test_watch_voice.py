import asyncio
import base64
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.application.telegram_voice_transcription import TelegramVoiceOutcome
from app.core.config import Settings
from app.infrastructure.watch_voice_github import (
    MarkInboxClient,
    WatchVoiceError,
    transcript_parts,
)
from app.models.watch_voice import (
    WatchVoiceCursor,
    WatchVoiceCycle,
    WatchVoiceIncident,
    WatchVoiceNote,
    WatchVoiceSource,
    WatchVoiceTextReceipt,
    WatchVoiceWorkClaim,
    WatchVoiceWorkResult,
)
from app.worker import watch_voice as worker
from app.worker.dropbox_calls import DropboxCallWorkerError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def settings(**extra):
    return Settings(
        GATE_WATCH_VOICE_ENABLED=True,
        GATE_WATCH_VOICE_DATA_GATE_APPROVED=True,
        GATE_WATCH_VOICE_WORKSPACE_KEY="mark-personal",
        DROPBOX_CALL_APP_KEY="synthetic-app",
        DROPBOX_CALL_APP_SECRET="synthetic-secret",
        DROPBOX_CALL_REFRESH_TOKEN="synthetic-refresh",
        DROPBOX_CALL_ACCOUNT_ID="synthetic-owner",
        OPENAI_API_KEY="synthetic-openai",
        GATE_MARK_GITHUB_TOKEN="synthetic-github",
        GATE_MARK_GITHUB_WRITER="synthetic-writer",
        **extra,
    )


def exact_settings():
    """Legacy acceptance cases authorize only their one synthetic source revision."""
    return settings(
        GATE_WATCH_VOICE_FILE_ID="id:synthetic",
        GATE_WATCH_VOICE_REVISION="synthetic-revision",
    )


def test_worker_rejects_dual_driver_before_any_io(monkeypatch):
    def unexpected_client(*args, **kwargs):
        raise AssertionError("conflicting driver reached GitHub client")

    monkeypatch.setattr(worker, "MarkInboxClient", unexpected_client)
    factory, http = AsyncMock(), AsyncMock()
    conflicting = settings().model_copy(update={"gate_watch_voice_actions_enabled": True})
    with pytest.raises(WatchVoiceError, match="^watch_voice_driver_conflict$"):
        asyncio.run(worker.process_watch_voice(conflicting, factory, http))
    factory.assert_not_called()
    assert http.mock_calls == []


async def admitted_future_cursor(factory, cutover, *, cursor="synthetic-cursor"):
    """Fixture for a cutover already admitted by the separate Actions binding."""
    async with factory() as session:
        session.add(WatchVoiceCursor(
            workspace_key="mark-personal", account_id="synthetic-owner",
            cursor=cursor, future_cutover_at=cutover,
        ))
        await session.commit()


@asynccontextmanager
async def database(tmp_path):
    url = os.getenv("DATABASE_URL")
    schema = "watch_test_" + uuid.uuid4().hex
    if url:
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        admin = None
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "watch.sqlite"))
    async with engine.begin() as connection:
        for table in (WatchVoiceCursor.__table__, WatchVoiceNote.__table__,
                      WatchVoiceCycle.__table__, WatchVoiceSource.__table__,
                      WatchVoiceIncident.__table__, WatchVoiceWorkResult.__table__,
                      WatchVoiceWorkClaim.__table__, WatchVoiceTextReceipt.__table__):
            await connection.run_sync(table.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        if admin:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


class World:
    """Synthetic Dropbox+GitHub endpoints; records effects, never reaches the network."""

    def __init__(self):
        self.audio = b"synthetic-original-audio"
        self.entry = {
            ".tag": "file",
            "id": "id:synthetic",
            "rev": "synthetic-revision",
            "name": "Synthetic.m4a",
            "path_display": "/Alpha/Voice/Synthetic.m4a",
            "size": len(self.audio),
            "server_modified": "2026-10-05T15:00:00Z",
        }
        self.issues = []
        self.comments = []
        self.posts = 0
        self.provider_calls = 0
        self.fail_post = None
        self.offline = False
        self.fail_download_once = False
        self.foreign_repo = False
        self.public_repo = False
        self.transcript = "Синтетическая запись. Проверить тестовую задачу."
        self.transcription_error = False
        self.calls = []

    def handle(self, request):
        self.calls.append((request.method, str(request.url)))
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == "/repos/markmalina/mark" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "full_name": "other/repo" if self.foreign_repo else "markmalina/mark",
                    "private": not self.public_repo,
                    "archived": False,
                    "has_issues": True,
                },
            )
        if path == "/user":
            return httpx.Response(200, json={"login": "synthetic-writer"})
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "synthetic-access"})
        if path.endswith("/users/get_current_account"):
            return httpx.Response(200, json={"account_id": "synthetic-owner"})
        if path.endswith("/files/list_folder") or path.endswith("/files/list_folder/continue"):
            return httpx.Response(
                200,
                json={
                    "entries": [self.entry, self.entry],
                    "cursor": "synthetic-cursor",
                    "has_more": False,
                },
            )
        if path.endswith("/files/download"):
            if self.fail_download_once:
                self.fail_download_once = False
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(
                200, content=self.audio, headers={"content-type": "application/octet-stream"}
            )
        if path == "/repos/markmalina/mark/issues" and request.method == "GET":
            return httpx.Response(200, json=self.issues)
        if path.endswith("/comments") and request.method == "GET":
            return httpx.Response(200, json=self.comments)
        if request.method == "POST" and path.startswith("/repos/markmalina/mark/issues"):
            self.posts += 1
            if self.fail_post == "reject":
                return httpx.Response(403, json={"message": "synthetic rejection"})
            payload = json.loads(request.content)
            obj = {
                "number": 101,
                "body": payload["body"],
                "created_at": "2026-10-05T15:05:00Z",
                "user": {"login": "synthetic-writer"},
                "html_url": "https://github.com/markmalina/mark/issues/101",
            }
            if path.endswith("/comments"):
                obj["html_url"] += "#issuecomment-" + str(len(self.comments) + 1)
                self.comments.append(obj)
            else:
                self.issues.append(obj)
            if self.fail_post == "lost_ack":
                self.fail_post = None
                raise httpx.ReadTimeout("lost ack", request=request)
            return httpx.Response(201, json=obj)
        raise AssertionError("Unexpected synthetic endpoint: " + path)

    def patches(self, monkeypatch):
        monkeypatch.setattr(worker, "_probe_duration_seconds", AsyncMock(return_value=5))
        world = self

        class Transcriber:
            def __init__(self, **kwargs):
                self.downloader = kwargs["downloader"]

            async def transcribe(self, command):
                await self.downloader.download(command.voice)
                assert command.reservation_key.startswith("watch-voice:")
                assert command.connector_ref == "telegram_business"
                world.provider_calls += 1
                if world.transcription_error:
                    raise RuntimeError("synthetic uncertain provider")
                return SimpleNamespace(
                    outcome=TelegramVoiceOutcome.TRANSCRIBED,
                    alpha_input_text=world.transcript,
                    reason_code="synthetic_transcribed",
                )

        monkeypatch.setattr(worker, "TelegramVoiceTranscriptionService", Transcriber)


async def notes(factory):
    async with factory() as session:
        return list((await session.scalars(select(WatchVoiceNote))).all())


def queued_note(world, file_id, received_at, **state):
    values = {
        "id": str(uuid.uuid4()),
        "workspace_key": "mark-personal",
        "account_id": "synthetic-owner",
        "file_id": file_id,
        "revision": "abcdef1234",
        "source_path": "/Alpha/Voice/Synthetic.m4a",
        "filename": "Synthetic.m4a",
        "media_type": "audio/mp4",
        "size_bytes": len(world.audio),
        "sha256": hashlib.sha256(world.audio).hexdigest(),
        "data": world.audio,
        "duration_seconds": 5,
        "recorded_at": None,
        "dropbox_modified_at": received_at,
        "received_at": received_at,
        "transcription_state": "received",
        "transcript": None,
        "delivery_state": "pending",
        "next_part": 0,
    }
    values.update(state)
    return WatchVoiceNote(**values)


def test_default_off_and_independent_calls_path():
    s = Settings()
    assert not s.gate_watch_voice_enabled
    assert not s.gate_watch_voice_data_gate_approved
    assert s.dropbox_call_inbox_path == "/Alpha/Calls"
    assert not s.gate_mark_github_token.get_secret_value()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"gate_watch_voice_data_gate_approved": False}, "data_gate"),
        ({"gate_watch_voice_workspace_key": "foreign"}, "workspace"),
        ({"openai_api_base_url": "https://foreign.invalid/v1"}, "provider"),
        ({"gate_telegram_voice_transcription_model": "gpt-4o-transcribe"}, "provider"),
    ],
)
def test_configuration_fails_closed(change, reason):
    with pytest.raises(WatchVoiceError, match=reason):
        worker.validate_settings(settings().model_copy(update=change))


@pytest.mark.parametrize("path", ["/Alpha/Calls/a.m4a", "/Alpha/VoiceOther/a.m4a", "/a.m4a"])
def test_source_path_is_exact(path):
    world = World()
    assert worker.source_entry({**world.entry, "path_display": path}) is None


def test_parts_preserve_all_unicode():
    source = "Пример 🎙️\n" * 15000
    parts = transcript_parts(source)
    assert "".join(parts) == source
    assert len(parts) > 1
    assert all(len(p.encode("utf-8")) <= 24000 for p in parts)


@pytest.mark.parametrize("size", [0, True, worker.SOURCE_LIMIT + 1])
def test_source_size_invalid_is_not_silently_skipped(size):
    with pytest.raises(WatchVoiceError, match="size_limit"):
        worker.source_entry({**World().entry, "size": size})


def test_full_cycle_preserves_original_transcript_times_and_deduplicates(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                for _ in range(3):
                    await worker.process_watch_voice(exact_settings(), factory, http)
            rows = await notes(factory)
            assert len(rows) == 1
            note = rows[0]
            assert note.data == world.audio
            assert note.sha256 == hashlib.sha256(world.audio).hexdigest()
            assert note.recorded_at is None
            assert note.dropbox_modified_at.date().isoformat() == "2026-10-05"
            assert note.transcript == world.transcript
            assert note.delivery_state == "delivered"
            assert note.issue_url == "https://github.com/markmalina/mark/issues/101"
            assert note.github_received_at.date().isoformat() == "2026-10-05"
            assert world.provider_calls == 1 and world.posts == 1
            assert world.transcript in world.issues[0]["body"]

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["row", "cursor", "cutover", "both"])
def test_future_worker_missing_admission_has_no_effects(tmp_path, monkeypatch, missing):
    world = World()
    world.patches(monkeypatch)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http,
        ):
            cutover = datetime.now(UTC) - timedelta(hours=1)
            if missing != "row":
                await admitted_future_cursor(
                    factory,
                    None if missing in {"cutover", "both"} else cutover,
                    cursor=None if missing in {"cursor", "both"} else "kept-cursor",
                )
            queued = queued_note(world, "id:already-queued", datetime.now(UTC))
            async with factory() as session:
                session.add(queued)
                await session.commit()
            with pytest.raises(WatchVoiceError, match="future_cutover_required"):
                await worker.process_watch_voice(settings(), factory, http)
            assert world.calls == []
            assert world.provider_calls == world.posts == 0
            assert (await notes(factory))[0].transcription_state == "received"
            async with factory() as session:
                state = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                if missing == "row":
                    assert state is None  # no implicit bootstrap or watermark installation
                else:
                    assert state.cursor == (
                        None if missing in {"cursor", "both"} else "kept-cursor"
                    )
                    assert (state.future_cutover_at is None) == (missing in {"cutover", "both"})

    asyncio.run(run())


def test_future_worker_excludes_old_dropbox_and_all_old_queue_effects(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)
    cutover = datetime.now(UTC) - timedelta(hours=2)
    before = cutover - timedelta(seconds=1)
    after = cutover + timedelta(seconds=1)

    def handle(request):
        if "/files/list_folder" in request.url.path:
            assert request.url.path.endswith("/continue")
            assert json.loads(request.content)["cursor"] == "kept-cursor"
            return httpx.Response(200, json={
                "entries": [
                    {**world.entry, "id": "id:old-delta", "server_modified": before.isoformat()},
                    {**world.entry, "id": "id:equal-delta", "server_modified": cutover.isoformat()},
                ],
                "cursor": "advanced-cursor", "has_more": False,
            })
        return world.handle(request)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http,
        ):
            await admitted_future_cursor(factory, cutover, cursor="kept-cursor")
            historical = []
            for i, (received, modified) in enumerate(
                ((before, after), (after, before), (after, cutover))
            ):
                for transcription, delivery in (
                    ("received", "pending"), ("transcribed", "pending"),
                    ("processing", "pending"), ("transcribed", "sending"),
                    ("transcribed", "unknown"),
                ):
                    historical.append(queued_note(
                        world, f"id:old-{i}-{transcription}-{delivery}", received,
                        dropbox_modified_at=modified, transcription_state=transcription,
                        delivery_state=delivery, attempted_at=before,
                        transcript=(
                            "Synthetic prior transcript" if transcription == "transcribed" else None
                        ),
                        last_error="synthetic-retained-journal",
                    ))
            async with factory() as session:
                session.add_all(historical)
                await session.commit()
            original = {
                note.id: {c.key: getattr(note, c.key) for c in WatchVoiceNote.__table__.columns}
                for note in await notes(factory)
            }
            await worker.process_watch_voice(settings(), factory, http)
            assert world.provider_calls == world.posts == 0
            assert not any(url.endswith("/files/download") for _, url in world.calls)
            assert {
                note.id: {c.key: getattr(note, c.key) for c in WatchVoiceNote.__table__.columns}
                for note in await notes(factory)
            } == original  # no quarantine, retry, claim reset or publication of history
            async with factory() as session:
                state = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert state.cursor == "advanced-cursor"
                assert state.future_cutover_at.replace(tzinfo=UTC) == cutover

    asyncio.run(run())


def test_future_worker_recovers_and_drains_batches_without_replay(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)
    cutover = datetime.now(UTC) - timedelta(minutes=1)
    entries = [
        {**world.entry, "id": f"id:future-{i}", "rev": f"future-revision-{i}",
         "server_modified": (cutover + timedelta(seconds=1)).isoformat()}
        for i in range(12)
    ]
    cursors = []
    fail_delta = True

    def handle(request):
        nonlocal fail_delta
        if "/files/list_folder" in request.url.path:
            assert request.url.path.endswith("/continue")
            cursors.append(json.loads(request.content)["cursor"])
            if fail_delta:
                fail_delta = False
                raise httpx.ConnectError("synthetic temporary outage", request=request)
            return httpx.Response(200, json={
                "entries": entries + entries[:2], "cursor": "next-future-cursor", "has_more": False,
            })
        response = world.handle(request)
        if request.method == "POST" and request.url.path == "/repos/markmalina/mark/issues":
            issue = world.issues[-1]
            issue["number"] = 100 + world.posts
            issue["html_url"] = f'https://github.com/markmalina/mark/issues/{issue["number"]}'
            return httpx.Response(201, json=issue)
        return response

    async def run():
        async with database(tmp_path) as factory:
            await admitted_future_cursor(factory, cutover, cursor="original-future-cursor")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                assert not await worker.process_watch_voice(settings(), factory, http)
                assert worker.OBSERVATION.scan_error_code == "watch_voice_delta_unavailable"
            assert not await notes(factory)
            async with factory() as session:
                state = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert state.cursor == "original-future-cursor"
                assert state.future_cutover_at.replace(tzinfo=UTC) == cutover
            for expected in (10, 12, 12):
                # A fresh process/client uses only persisted admission and cursor.
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                    await worker.process_watch_voice(settings(), factory, http)
                assert world.provider_calls == world.posts == expected
            rows = await notes(factory)
            assert len(rows) == len({row.issue_url for row in rows}) == 12
            assert all(row.delivery_state == "delivered" for row in rows)
            assert sum(url.endswith("/files/download") for _, url in world.calls) == 12
            assert cursors == ["original-future-cursor", "original-future-cursor",
                               "next-future-cursor", "next-future-cursor"]
            async with factory() as session:
                state = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert state.cursor == "next-future-cursor"
                assert state.future_cutover_at.replace(tzinfo=UTC) == cutover

    asyncio.run(run())


def test_lost_github_ack_recovers_one_issue_without_repost(tmp_path, monkeypatch):
    world = World()
    world.fail_post = "lost_ack"
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                assert (await notes(factory))[0].delivery_state == "unknown"
                await worker.process_watch_voice(exact_settings(), factory, http)
            assert (await notes(factory))[0].delivery_state == "delivered"
            assert world.posts == 1 and world.provider_calls == 1

    asyncio.run(run())


def test_unknown_not_found_never_blindly_posts(tmp_path, monkeypatch):
    world = World()
    world.fail_post = "lost_ack"
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                world.issues = []
                await worker.process_watch_voice(exact_settings(), factory, http)
            assert (await notes(factory))[0].delivery_state == "unknown"
            assert world.posts == 1

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["offline", "fail_download_once"])
def test_offline_reconnect_retains_queue_and_delivers_once(tmp_path, monkeypatch, fault):
    world = World()
    setattr(world, fault, True)
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                if fault == "offline":
                    # GitHub private-target preflight remains a mandatory fence.
                    with pytest.raises((WatchVoiceError, DropboxCallWorkerError)):
                        await worker.process_watch_voice(exact_settings(), factory, http)
                else:
                    assert not await worker.process_watch_voice(exact_settings(), factory, http)
                    async with factory() as session:
                        source = await session.scalar(select(WatchVoiceSource))
                        assert source.state == "retry"
                        assert source.last_error == "dropbox_call_download_transport_failed"
                        assert source.next_attempt_at is not None
                        # Advance the retry clock without waiting in the test.
                        await session.execute(update(WatchVoiceSource).values(
                            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
                        await session.commit()
                    assert not await notes(factory)
                world.offline = False
                await worker.process_watch_voice(exact_settings(), factory, http)
            assert len(await notes(factory)) == 1
            assert world.posts == 1 and world.provider_calls == 1

    asyncio.run(run())


def test_unknown_provider_is_not_repeated(tmp_path, monkeypatch):
    world = World()
    world.transcription_error = True
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                await worker.process_watch_voice(exact_settings(), factory, http)
            assert (await notes(factory))[0].transcription_state == "unknown"
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


def test_pending_publication_progresses_past_full_batch_of_unknowns(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)

    def handle(request):
        if "/files/list_folder" in request.url.path:
            return httpx.Response(
                200, json={"entries": [], "cursor": "synthetic-cursor", "has_more": False}
            )
        return world.handle(request)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http,
        ):
            now = datetime.now(UTC)
            await admitted_future_cursor(factory, now - timedelta(hours=1))
            unknowns = [
                queued_note(
                    world,
                    f"id:unknown-{i}",
                    now - timedelta(minutes=20 - i),
                    transcription_state="transcribed",
                    transcript=f"Synthetic unresolved {i}",
                    delivery_state="unknown",
                    attempted_at=now - timedelta(minutes=5),
                    last_error="github_post_unknown",
                )
                for i in range(10)
            ]
            pending = queued_note(
                world,
                "id:pending-after-unknowns",
                now,
                transcription_state="transcribed",
                transcript="Synthetic actionable publication",
            )
            async with factory() as session:
                session.add_all([*unknowns, pending])
                await session.commit()
            before = {
                note.id: tuple(
                    getattr(note, key) for key in ("data", "sha256", "next_part", "last_error")
                )
                for note in await notes(factory)
                if note.delivery_state == "unknown"
            }
            await worker.process_watch_voice(settings(), factory, http)
            rows = await notes(factory)
            assert (
                next(note for note in rows if note.id == pending.id).delivery_state == "delivered"
            )
            assert world.posts == 1 and world.provider_calls == 0
            for note in rows:
                if note.id in before:
                    assert note.delivery_state == "unknown"
                    assert (
                        tuple(
                            getattr(note, key)
                            for key in ("data", "sha256", "next_part", "last_error")
                        )
                        == before[note.id]
                    )

    asyncio.run(run())


@pytest.mark.parametrize("exhausted", [True, False])
def test_normalization_crossing_month_uses_current_processing_budget(
    tmp_path, monkeypatch, exhausted
):
    from app.application.telegram_voice_transcription import NormalizedVoice, ProviderTranscript
    from app.models.billing import (
        BillingBudgetReservationModel,
        BillingEntitlementModel,
        BillingLedgerEntryModel,
        BillingUsageEventModel,
    )
    from app.models.canonical import Workspace

    world = World()
    received = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
    november = datetime(2026, 11, 1, tzinfo=UTC)
    clock = [received]

    class ProcessingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    class Normalizer:
        async def normalize(self, voice):
            clock[0] = november
            return NormalizedVoice(b"synthetic-normalized", "Synthetic.mp3", "audio/mpeg")

    class Provider:
        ready = True

        def __init__(self, **kwargs):
            pass

        async def transcribe(self, voice, *, language):
            world.provider_calls += 1
            clock[0] = november + timedelta(seconds=1)
            return ProviderTranscript("Synthetic transcript", billed_quantity=1, actual_microusd=10)

    monkeypatch.setattr(worker, "datetime", ProcessingClock)
    monkeypatch.setattr(worker, "TelegramVoiceNormalizer", Normalizer)
    monkeypatch.setattr(worker, "OpenAITranscriptionClient", Provider)

    async def run():
        async with database(tmp_path) as factory:
            workspace_id = uuid.uuid4()
            held_id = uuid.uuid4()
            note = queued_note(world, "id:october-source", received)
            async with factory() as session:
                for table in (
                    Workspace.__table__,
                    BillingEntitlementModel.__table__,
                    BillingBudgetReservationModel.__table__,
                    BillingUsageEventModel.__table__,
                    BillingLedgerEntryModel.__table__,
                ):
                    await session.run_sync(lambda s, table=table: table.create(s.connection()))
                session.add(
                    Workspace(
                        id=workspace_id,
                        key="mark-personal",
                        display_name="Synthetic owner",
                        is_active=True,
                    )
                )
                await session.flush()
                session.add(
                    BillingEntitlementModel(
                        workspace_id=workspace_id,
                        entitlement_key="synthetic-budget",
                        product="gate",
                        scope_kind="telegram_connector",
                        scope_ref="telegram_business",
                        plan_key="synthetic",
                        state="active",
                        valid_from=received - timedelta(days=1),
                        hard_limit_microusd=3_000_000,
                    )
                )
                if exhausted:
                    session.add(
                        BillingBudgetReservationModel(
                            id=held_id,
                            workspace_id=workspace_id,
                            reservation_key="synthetic-other-november-usage",
                            product="gate",
                            operation="transcription",
                            scope_kind="telegram_connector",
                            scope_ref="telegram_business",
                            period_key="2026-11",
                            reserved_microusd=3_000_000,
                            settled_microusd=3_000_000,
                            state="settled",
                            created_at=november,
                            updated_at=november,
                        )
                    )
                session.add(note)
                await session.commit()
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.transcribe_note(settings(), factory, http, note.id)
                # The rejected/transcribed claim is never rearmed by another cycle.
                await worker.transcribe_note(settings(), factory, http, note.id)
            stored = (await notes(factory))[0]
            assert stored.received_at.replace(tzinfo=UTC) == received
            assert world.provider_calls == (0 if exhausted else 1)
            async with factory() as session:
                reservations = list(
                    (await session.scalars(select(BillingBudgetReservationModel))).all()
                )
                assert len(reservations) == 1
                reservation = reservations[0]
                assert reservation.period_key == "2026-11"
                assert reservation.state == "settled"
                if exhausted:
                    assert reservation.id == held_id and reservation.settled_microusd == 3_000_000
                    assert (
                        stored.transcription_state == "rejected"
                        and stored.last_error == "billing_hard_limit_exceeded"
                    )
                else:
                    assert stored.transcription_state == "transcribed"
                    assert reservation.reservation_key == f"watch-voice:{note.id}"
                    assert reservation.created_at.replace(tzinfo=UTC) == november
                    assert reservation.updated_at.replace(tzinfo=UTC) == november + timedelta(
                        seconds=1
                    )
                    assert reservation.settled_microusd == 10

    asyncio.run(run())


@pytest.mark.parametrize("suffix", ["recovery1", "postpayment1"])
def test_recovery_normalization_cannot_move_its_accepted_month(monkeypatch, suffix):
    from app.application.telegram_voice_transcription import (
        DownloadedVoice,
        NormalizedVoice,
        TelegramVoiceReference,
        TelegramVoiceTranscriptionService,
        TranscribeTelegramVoiceCommand,
    )

    october = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
    clock = [october]
    attempts = []

    class ProcessingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    class Downloader:
        async def download(self, voice):
            return DownloadedVoice(b"synthetic-source", "Synthetic.m4a", "audio/mp4", 5)

    class Normalizer:
        async def normalize(self, voice):
            clock[0] = datetime(2026, 11, 1, tzinfo=UTC)
            return NormalizedVoice(b"synthetic-normalized", "Synthetic.mp3", "audio/mpeg")

    class Provider:
        ready = True
        transcribe = AsyncMock()

    def no_database():
        attempts.append("database")
        raise AssertionError("expired recovery must stop before SQL")

    monkeypatch.setattr(worker, "datetime", ProcessingClock)
    provider = Provider()
    service = TelegramVoiceTranscriptionService(
        downloader=Downloader(),
        normalizer=Normalizer(),
        speech_to_text=provider,
        budget=worker.WatchVoiceProcessingBudget(no_database, recovery_period="2026-10"),
    )
    command = TranscribeTelegramVoiceCommand(
        voice=TelegramVoiceReference("id:synthetic", "abc123", 16, 5),
        workspace_key="mark-personal",
        connector_ref="telegram_business",
        reservation_key=f"watch-voice:synthetic:{suffix}",
        period_key="2026-10",
        requested_microusd=5000,
        occurred_at=october,
        language="ru",
        enabled=True,
    )
    result = asyncio.run(service.transcribe(command))
    assert result.outcome == TelegramVoiceOutcome.REJECTED
    assert result.reason_code == "watch_voice_recovery_period_changed"
    assert command.reservation_key == f"watch-voice:synthetic:{suffix}"
    provider.transcribe.assert_not_awaited()
    assert attempts == []


def test_month_change_while_budget_reserves_releases_only_its_new_unspent_claim(monkeypatch):
    from app.application.billing import (
        BillingProduct,
        BudgetAdmission,
        BudgetReservation,
        BudgetReservationState,
        ReserveBudgetCommand,
        UsageAttribution,
        UsageOperation,
    )

    october = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
    november = datetime(2026, 11, 1, tzinfo=UTC)
    clock = [october]
    attribution = UsageAttribution(
        "mark-personal", BillingProduct.GATE, "telegram_connector", "telegram_business"
    )
    command = ReserveBudgetCommand(
        "watch-voice:synthetic", attribution, UsageOperation.TRANSCRIPTION, 5000, october
    )
    reservation = BudgetReservation(
        command.reservation_key,
        attribution,
        UsageOperation.TRANSCRIPTION,
        "2026-10",
        5000,
        BudgetReservationState.RESERVED,
        october,
        october,
    )

    class ProcessingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    async def reserve(*args, **kwargs):
        clock[0] = november
        return BudgetAdmission(True, "billing_reserved", reservation)

    release = AsyncMock()
    monkeypatch.setattr(worker, "datetime", ProcessingClock)
    monkeypatch.setattr(worker.SqlBillingBudgetService, "reserve", reserve)
    monkeypatch.setattr(worker.SqlBillingBudgetService, "release", release)
    budget = worker.WatchVoiceProcessingBudget(None, recovery_period=None)
    result = asyncio.run(budget.reserve(command, period_key="2026-10"))
    assert not result.allowed and result.reason_code == "watch_voice_processing_period_changed"
    release.assert_awaited_once_with(reservation, occurred_at=november)

    async def replay(*args, **kwargs):
        clock[0] = november
        return BudgetAdmission(False, "billing_reservation_replayed")

    clock[0] = october
    release.reset_mock()
    monkeypatch.setattr(worker.SqlBillingBudgetService, "reserve", replay)
    repeated = asyncio.run(budget.reserve(command, period_key="2026-10"))
    assert not repeated.allowed and repeated.reason_code == "billing_reservation_replayed"
    release.assert_not_awaited()


@pytest.mark.parametrize("flag", ["public_repo", "foreign_repo"])
def test_private_target_preflight_precedes_audio_processing(tmp_path, monkeypatch, flag):
    world = World()
    setattr(world, flag, True)
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                with pytest.raises(WatchVoiceError, match="mismatch"):
                    await worker.process_watch_voice(exact_settings(), factory, http)
            assert not await notes(factory)
            assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(run())


def test_long_transcript_all_parts_and_lost_comment_ack(tmp_path, monkeypatch):
    world = World()
    world.transcript = "Часть текста 🎙️\n" * 4000
    world.patches(monkeypatch)
    original = world.handle
    failed = False

    def handle(req):
        nonlocal failed
        if req.method == "POST" and req.url.path.endswith("/comments") and not failed:
            failed = True
            world.fail_post = "lost_ack"
        return original(req)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                for _ in range(3):
                    await worker.process_watch_voice(exact_settings(), factory, http)
            assert (await notes(factory))[0].delivery_state == "delivered"
            assert world.posts == len(transcript_parts(world.transcript))
            bodies = [world.issues[0]["body"]] + [c["body"] for c in world.comments]
            restored = bodies[0].split("Расшифровка, часть 1/", 1)[1].split(":\n\n", 1)[1]
            restored += "".join(b.split(":\n\n", 1)[1] for b in bodies[1:])
            assert restored == world.transcript

    asyncio.run(run())


def test_rejected_issue_keeps_original_and_requires_permission_fix(tmp_path, monkeypatch):
    world = World()
    world.fail_post = "reject"
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                await worker.process_watch_voice(exact_settings(), factory, http)
            row = (await notes(factory))[0]
            assert row.delivery_state == "blocked" and row.data == world.audio
            assert world.posts == 1 and world.provider_calls == 1

    asyncio.run(run())


def test_cross_workspace_status_does_not_query_payload(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    result = asyncio.run(worker.operational_status(session, settings(), scope_allowed=False))
    assert result["delivery_counts"] == {}
    session.execute.assert_not_awaited()


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="Real PostgreSQL concurrency required")
def test_postgres_parallel_workers_one_provider_one_issue(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await worker.ensure_cursor(settings(), factory)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await asyncio.gather(
                    *(worker.process_watch_voice(exact_settings(), factory, http) for _ in range(4))
                )
            assert len(await notes(factory)) == 1
            assert world.provider_calls == 1 and world.posts == 1

    asyncio.run(run())


def test_receipt_rejects_foreign_writer():
    client = MarkInboxClient(None, "synthetic", "expected")
    with pytest.raises(ValueError):
        client.receipt(
            {
                "number": 1,
                "html_url": "https://github.com/markmalina/mark/issues/1",
                "body": "test",
                "user": {"login": "other"},
            },
            part=0,
            issue_number=None,
            expected_body="test",
        )


def test_stale_claim_quarantined_and_reconciled_without_repost(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                async with factory() as session:
                    note = (await session.scalars(select(WatchVoiceNote))).one()
                    note.delivery_state = "sending"
                    note.next_part = 0
                    note.attempted_at = datetime.now(UTC) - timedelta(hours=2)
                    await session.commit()
                await worker.process_watch_voice(exact_settings(), factory, http)
            assert (await notes(factory))[0].delivery_state == "delivered"
            assert world.posts == 1

    asyncio.run(run())


def test_definite_rejection_resumed_once_without_retranscription(tmp_path, monkeypatch):
    world = World()
    world.fail_post = "reject"
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
                row = (await notes(factory))[0]
                assert await worker.resume_rejected_delivery(settings(), factory, row.id)
                assert not await worker.resume_rejected_delivery(settings(), factory, row.id)
                world.fail_post = None
                await worker.process_watch_voice(exact_settings(), factory, http)
                assert not await worker.resume_rejected_delivery(settings(), factory, row.id)
            assert (await notes(factory))[0].delivery_state == "delivered"
            assert len(world.issues) == 1
            assert world.posts == 2 and world.provider_calls == 1

    asyncio.run(run())


def test_exact_rollout_source_skips_other_revisions(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(
                    settings(
                        GATE_WATCH_VOICE_FILE_ID="id:another",
                        GATE_WATCH_VOICE_REVISION="another-revision",
                    ),
                    factory,
                    http,
                )
            assert not await notes(factory)
            assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("queued_state", ["received", "transcribed", "blocked"])
def test_exact_rollout_does_not_process_preexisting_other_note(tmp_path, monkeypatch, queued_state):
    world = World()
    world.patches(monkeypatch)
    selected = settings(
        GATE_WATCH_VOICE_FILE_ID=world.entry["id"],
        GATE_WATCH_VOICE_REVISION=world.entry["rev"],
    )

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                # A previous broader rollout stored an unrelated revision before the cap.
                other_entry = dict(world.entry, id="id:other", rev="other-revision")
                await worker.store_entry(settings(), factory, http, "synthetic", other_entry)
                other = (await notes(factory))[0]
                async with factory() as session:
                    queued = await session.get(WatchVoiceNote, other.id)
                    queued.transcription_state = (
                        "received" if queued_state == "received" else "transcribed"
                    )
                    if queued_state != "received":
                        queued.transcript = "Unrelated queued transcript"
                        queued.transcribed_at = datetime.now(UTC)
                    if queued_state == "blocked":
                        queued.delivery_state = "blocked"
                        queued.last_error = "github_post_rejected"
                    await session.commit()
                if queued_state == "blocked":
                    assert not await worker.resume_rejected_delivery(selected, factory, other.id)
                for _ in range(3):
                    await worker.process_watch_voice(selected, factory, http)
            rows = {row.file_id: row for row in await notes(factory)}
            assert len(rows) == 2
            assert rows[world.entry["id"]].delivery_state == "delivered"
            assert rows["id:other"].transcription_state == (
                "received" if queued_state == "received" else "transcribed"
            )
            assert rows["id:other"].delivery_state == (
                "blocked" if queued_state == "blocked" else "pending"
            )
            assert world.provider_calls == 1 and world.posts == 1
            assert len(world.issues) == 1

    asyncio.run(run())


def test_gate_original_receipt_and_foreign_scope_denial(tmp_path, monkeypatch):
    from app.api import mcp_routes

    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(exact_settings(), factory, http)
            row = (await notes(factory))[0]
            principal = SimpleNamespace(
                access_scope=SimpleNamespace(
                    workspace_id=settings().gate_workspace_id,
                    project_ids={mcp_routes.UNCLASSIFIED_PROJECT_ID},
                )
            )
            async with factory() as session:
                response = await mcp_routes._watch_voice_tool_result(
                    request_id="test",
                    selector="watch-voice:" + row.id,
                    principal=principal,
                    session=session,
                    settings=settings(),
                )
                payload = json.loads(response["result"]["content"][0]["text"])
                assert payload["transcript"] == world.transcript
                assert payload["audio_sha256"] == hashlib.sha256(world.audio).hexdigest()
                response = await mcp_routes._watch_voice_tool_result(
                    request_id="test",
                    selector="watch-voice-file:" + row.id,
                    principal=principal,
                    session=session,
                    settings=settings(),
                )
                assert response["result"]["isError"] is False
                original = json.loads(response["result"]["content"][0]["text"])
                assert base64.b64decode(original["data_base64"]) == world.audio
            principal.access_scope.workspace_id = "foreign"
            session = SimpleNamespace(scalar=AsyncMock())
            denied = await mcp_routes._watch_voice_tool_result(
                request_id="test",
                selector="watch-voice-file:" + row.id,
                principal=principal,
                session=session,
                settings=settings(),
            )
            assert denied["result"]["isError"] is True
            session.scalar.assert_not_awaited()

    asyncio.run(run())

