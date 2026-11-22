"""Offline signed callback and writer integration on a disposable database."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from app.api.deps import get_db_session
from app.api.dropbox_call_routes import router
from app.core.config import get_settings
from app.infrastructure import watch_voice_early_cards as early
from app.infrastructure.watch_app_auth import WatchAppInstallation
from app.infrastructure.watch_voice_github import GithubReceipt, WatchVoiceError
from app.models.domain import DropboxCallInboxState
from app.models.watch_voice import (
    WatchEarlyCard,
    WatchEarlyCursor,
    WatchEarlyRejection,
    WatchVoiceNote,
)
from app.worker import watch_voice, watch_webhook, watch_webhook_archive
from app.worker.dropbox_calls import DropboxCallWorkerError
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_signed_wake_restart_replay_batch_and_recovery(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        locking = True

        @event.listens_for(engine.sync_engine, "connect")
        def register_test_lock(connection, _):
            connection.create_function("pg_try_advisory_xact_lock", 1, lambda _: locking)
            connection.create_function("pg_advisory_xact_lock", 1, lambda _: None)

        async with engine.begin() as connection:
            for model in (
                DropboxCallInboxState,
                WatchEarlyCursor,
                WatchEarlyCard,
                WatchEarlyRejection,
                WatchVoiceNote,
            ):
                await connection.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal",
            dropbox_call_account_id="owner",
            dropbox_call_app_secret=SecretStr("synthetic"),
            gate_dropbox_call_intake_enabled=True,
            gate_watch_webhook_enabled=True,
            gate_watch_webhook_archive_enabled=True,
        )
        pk = ("mark-personal", "owner")
        now = datetime.now(UTC)
        async with factory.begin() as session:
            session.add(
                WatchEarlyCursor(
                    workspace_key=pk[0],
                    account_id=pk[1],
                    cursor="watch",
                    cutover_at=now - timedelta(minutes=1),
                    writer_mode="app_webhook",
                )
            )
            session.add(DropboxCallInboxState(account_id=pk[1], cursor="calls"))

        app = FastAPI()
        app.include_router(router)
        app.state.watch_wake_event = asyncio.Event()
        archive_wake = asyncio.Event()
        mark_wake = asyncio.Event()
        speech_wake = asyncio.Event()

        async def get_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = get_session
        app.dependency_overrides[get_settings] = lambda: settings

        async def callback(http, account, *, valid=True):
            body = json.dumps({"list_folder": {"accounts": [account]}}).encode()
            signature = hmac.digest(b"synthetic", body, hashlib.sha256).hex()
            return await http.post(
                "/dropbox/calls/webhook",
                content=body,
                headers={"X-Dropbox-Signature": signature if valid else "bad"},
            )

        def source(name, directory="Voice"):
            return {
                ".tag": "file",
                "path_display": f"/Alpha/{directory}/{name}.m4a",
                "name": name + ".m4a",
                "id": "id:" + name,
                "rev": "abc",
                "size": 10,
                "server_modified": now.isoformat(),
            }

        entries = [source("first"), source("second"), source("call", "Calls")]
        requests = []

        def dropbox(request):
            requests.append(json.loads(request.content)["cursor"])
            assert request.url.path == "/2/files/list_folder/continue"
            return httpx.Response(
                200,
                json={
                    "entries": entries,
                    "cursor": f"watch-{len(requests)}",
                    "has_more": False,
                },
            )

        class Inbox:
            posts = []
            reconciliations = []

            def __init__(self, *args, **kwargs):
                pass

            async def preflight(self):
                assert mark_wake.is_set()

            async def post(self, *, part, issue_number, title, body):
                self.posts.append(body)
                if len(self.posts) == 1:
                    raise WatchVoiceError("lost_ack", unknown=True)
                number = len(self.posts)
                return GithubReceipt(
                    number, f"https://github.com/markmalina/mark/issues/{number}", now
                )

            async def reconcile(self, *, part, issue_number, body):
                self.reconciliations.append(body)
                return GithubReceipt(1, "https://github.com/markmalina/mark/issues/1", now)

        async def token(*args):
            return "synthetic"

        async def account(*args):
            pass

        downloads = []

        async def download(**kwargs):
            name = kwargs["fallback_name"]
            downloads.append(name)
            if name == "missed.m4a" and downloads.count(name) == 1:
                raise DropboxCallWorkerError("dropbox_call_download_transport_failed")
            return b"test audio", "audio/mp4"

        async def duration(data):
            assert data == b"test audio"
            return 2

        monkeypatch.setattr(early, "_refresh_access_token", token)
        monkeypatch.setattr(early, "_verify_account_id", account)
        monkeypatch.setattr(watch_webhook_archive, "_refresh_access_token", token)
        monkeypatch.setattr(watch_webhook_archive, "_verify_account_id", account)
        monkeypatch.setattr(watch_voice, "_download_revision", download)
        monkeypatch.setattr(watch_voice, "_probe_duration_seconds", duration)
        monkeypatch.setattr(watch_webhook, "issue_mark_token", token)
        monkeypatch.setattr(watch_webhook, "AppInboxClient", Inbox)
        installation = WatchAppInstallation(1, 2, "synthetic")
        try:
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as ingress,
                httpx.AsyncClient(transport=httpx.MockTransport(dropbox)) as provider,
            ):
                assert (await callback(ingress, "owner", valid=False)).status_code == 401
                assert (await callback(ingress, "other")).status_code == 200
                assert not app.state.watch_wake_event.is_set()
                assert (await callback(ingress, "owner")).status_code == 200
                assert (await callback(ingress, "owner")).status_code == 200
                assert app.state.watch_wake_event.is_set()
                assert not requests and not Inbox.posts
                async with factory() as session:
                    cursor = await session.get(WatchEarlyCursor, pk)
                    calls = await session.get(DropboxCallInboxState, pk[1])
                    assert cursor.wake_generation == 2 and cursor.wake_processed_generation == 0
                    assert cursor.cursor == "watch" and calls.cursor == "calls" and calls.pending

                # Drop the in-memory signal, as on restart. The database signal survives.
                app.state.watch_wake_event = asyncio.Event()
                locking = False
                discover = (settings, factory, provider, archive_wake, mark_wake)
                publish = (settings, factory, provider, "synthetic", installation)
                assert not await watch_webhook.publish_watch_pass(*publish)
                assert not requests
                assert not await watch_webhook.discover_watch_pass(*discover)
                assert archive_wake.is_set() and not Inbox.posts
                await watch_webhook_archive.archive_watch_pass(
                    settings, factory, provider, speech_wake
                )
                assert speech_wake.is_set() and not Inbox.posts
                locking = True
                assert await watch_webhook.publish_watch_pass(*publish)  # first POST response lost
                assert not await watch_webhook.publish_watch_pass(*publish)  # reconcile then second
                assert len(Inbox.posts) == 2 and Inbox.reconciliations == Inbox.posts[:1]
                async with factory() as session:
                    cursor = await session.get(WatchEarlyCursor, pk)
                    cards = (await session.scalars(select(WatchEarlyCard))).all()
                    notes = (await session.scalars(select(WatchVoiceNote))).all()
                    assert cursor.wake_processed_generation == cursor.wake_generation == 2
                    assert len(cards) == 2 and all(
                        card.wake_received_at is not None
                        and card.state == "saved"
                        and card.archive_input is None
                        for card in cards
                    )
                    assert {note.id for note in notes} == {card.id for card in cards}
                    assert all(
                        note.webhook_archived and note.transcription_state == "received"
                        and note.transcript is None and note.data == b"test audio"
                        for note in notes
                    )
                    assert sorted(downloads) == ["first.m4a", "second.m4a"]

                # A file arriving with no webhook uses the exact same recovery path.
                entries.append(source("missed"))
                assert not await watch_webhook.discover_watch_pass(*discover)
                await watch_webhook_archive.archive_watch_pass(settings, factory, provider)
                assert not await watch_webhook.publish_watch_pass(*publish)
                async with factory.begin() as session:
                    missed = await session.scalar(
                        select(WatchEarlyCard).where(WatchEarlyCard.file_id == "id:missed")
                    )
                    assert missed.state == "saved" and missed.wake_received_at is None
                    assert missed.archive_input is not None and missed.archive_retry_at is not None
                    assert await session.get(WatchVoiceNote, missed.id) is None
                    missed.archive_retry_at = now - timedelta(minutes=1)
                assert not await watch_webhook_archive.archive_watch_pass(
                    settings, factory, provider
                )
                async with factory() as session:
                    missed_note = await session.get(WatchVoiceNote, missed.id)
                    assert missed_note is not None and missed_note.transcription_state == "received"
                assert len(Inbox.posts) == 3
                assert sorted(downloads) == [
                    "first.m4a", "missed.m4a", "missed.m4a", "second.m4a"
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_worker_uses_signal_before_recovery_timeout(monkeypatch):
    async def scenario():
        wake = asyncio.Event()
        passes = asyncio.Queue()

        async def run_pass(*args):
            passes.put_nowait(None)
            return False

        monkeypatch.delenv("GATE_WATCH_APP_PRIVATE_KEY", raising=False)
        monkeypatch.setattr(watch_webhook, "discover_watch_pass", run_pass)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal",
            gate_watch_voice_enabled=False,
            dropbox_call_account_id="owner",
        )
        task = asyncio.create_task(
            watch_webhook.run_watch_webhook_loop(settings, None, wake, None, asyncio.Event())
        )
        try:
            await asyncio.wait_for(passes.get(), timeout=2)
            wake.set()
            await asyncio.wait_for(passes.get(), timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
