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
from app.models.watch_voice import WatchVoiceCursor, WatchVoiceNote
from app.worker import watch_voice as worker
from app.worker.dropbox_calls import DropboxCallWorkerError
from sqlalchemy import select, text
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
        for table in (WatchVoiceCursor.__table__, WatchVoiceNote.__table__):
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
                    await worker.process_watch_voice(settings(), factory, http)
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


def test_lost_github_ack_recovers_one_issue_without_repost(tmp_path, monkeypatch):
    world = World()
    world.fail_post = "lost_ack"
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(settings(), factory, http)
                assert (await notes(factory))[0].delivery_state == "unknown"
                await worker.process_watch_voice(settings(), factory, http)
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
                await worker.process_watch_voice(settings(), factory, http)
                world.issues = []
                await worker.process_watch_voice(settings(), factory, http)
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
                with pytest.raises((WatchVoiceError, DropboxCallWorkerError)):
                    await worker.process_watch_voice(settings(), factory, http)
                world.offline = False
                await worker.process_watch_voice(settings(), factory, http)
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
                await worker.process_watch_voice(settings(), factory, http)
                await worker.process_watch_voice(settings(), factory, http)
            assert (await notes(factory))[0].transcription_state == "unknown"
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("flag", ["public_repo", "foreign_repo"])
def test_private_target_preflight_precedes_audio_processing(tmp_path, monkeypatch, flag):
    world = World()
    setattr(world, flag, True)
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                with pytest.raises(WatchVoiceError, match="mismatch"):
                    await worker.process_watch_voice(settings(), factory, http)
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
                    await worker.process_watch_voice(settings(), factory, http)
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
                await worker.process_watch_voice(settings(), factory, http)
                await worker.process_watch_voice(settings(), factory, http)
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
                    *(worker.process_watch_voice(settings(), factory, http) for _ in range(4))
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
                await worker.process_watch_voice(settings(), factory, http)
                async with factory() as session:
                    note = (await session.scalars(select(WatchVoiceNote))).one()
                    note.delivery_state = "sending"
                    note.next_part = 0
                    note.attempted_at = datetime.now(UTC) - timedelta(hours=2)
                    await session.commit()
                await worker.process_watch_voice(settings(), factory, http)
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
                await worker.process_watch_voice(settings(), factory, http)
                row = (await notes(factory))[0]
                assert await worker.resume_rejected_delivery(settings(), factory, row.id)
                assert not await worker.resume_rejected_delivery(settings(), factory, row.id)
                world.fail_post = None
                await worker.process_watch_voice(settings(), factory, http)
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


def test_gate_original_receipt_and_foreign_scope_denial(tmp_path, monkeypatch):
    from app.api import mcp_routes

    world = World()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.process_watch_voice(settings(), factory, http)
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
