"""Synthetic early-card ordering, durable uncertain POSTs and same-issue text."""

import asyncio
import copy
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.infrastructure.watch_voice_cards import (
    card_status,
    drain_cards,
    publish_source_card,
    save_card_receipt,
)
from app.infrastructure.watch_voice_github import (
    GithubReceipt,
    MarkInboxClient,
    WatchVoiceError,
    marker,
    transcript_parts,
)
from app.infrastructure.watch_voice_sources import archive_source, discover
from app.models.watch_voice import WatchVoiceDrainRun, WatchVoiceSource
from app.worker import watch_voice as worker
from sqlalchemy import create_engine, select
from tests.test_watch_voice import World, database, notes, queued_note, settings
from tests.test_watch_voice_actions import (
    ActionsWorld,
    action_settings,
    claims,
    drain,
)


def early_settings():
    return settings(
        GATE_WATCH_VOICE_EARLY_INBOX_ENABLED=True,
        GATE_WATCH_VOICE_FILE_ID="id:synthetic",
        GATE_WATCH_VOICE_REVISION="synthetic-revision",
    )


class EarlyWorld(World):
    def __init__(self):
        super().__init__()
        self.hide_receipts = False

    def handle(self, request):
        if request.url.path.endswith("/files/download"):
            assert len(self.issues) == 1, "original download preceded metadata issue"
            assert self.provider_calls == 0
        if self.hide_receipts and request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx.Response(200, json=[])
        response = super().handle(request)
        # Synthetic receipt clocks follow discovery, independently of file mtime.
        if (request.method == "POST"
                and request.url.path.startswith("/repos/markmalina/mark/issues")):
            if response.status_code == 201:
                obj = response.json()
                obj["created_at"] = datetime.now(UTC).isoformat()
                (self.comments if request.url.path.endswith("/comments") else self.issues)[-1] = obj
                return httpx.Response(201, json=obj)
        return response


def test_early_issue_precedes_archive_and_stt_and_full_text_uses_same_issue(tmp_path, monkeypatch):
    world = EarlyWorld()
    world.transcript = "Полный текст 🎙️\n" * 4000
    world.patches(monkeypatch)

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            for _ in range(3):
                await worker.process_watch_voice(early_settings(), factory, http)
            note, = await notes(factory)
            async with factory() as session:
                source = await session.get(WatchVoiceSource, note.id)
                status = await card_status(session, early_settings())
            assert note.data == world.audio and note.transcript == world.transcript
            assert len(world.issues) == 1
            assert "Аудио получено, расшифровка ожидается" in world.issues[0]["body"]
            assert f"watch-voice-file:{note.id}" in world.issues[0]["body"]
            parts = transcript_parts(world.transcript)
            assert len(world.comments) == len(parts)
            assert note.next_part == len(parts) and note.delivery_state == "delivered"
            for index, (part, comment) in enumerate(zip(parts, world.comments, strict=True), 1):
                assert marker(note.id, index) in comment["body"] and part in comment["body"]
                assert comment["html_url"].startswith(note.issue_url + "#issuecomment-")
            assert source.card_issue_number == note.issue_number == 101
            assert source.card_received_at <= note.github_received_at
            assert status["counts"] == {"saved": 1}
            assert status["detection_to_card_seconds"]["samples"] == 1
            assert world.provider_calls == 1

    asyncio.run(run())


def test_saved_metadata_binds_archive_before_text(tmp_path, monkeypatch):
    world = EarlyWorld()
    world.patches(monkeypatch)

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            cfg = early_settings()
            key = await discover(cfg, factory, world.entry, None)
            client = MarkInboxClient(http, "synthetic", "synthetic-writer")
            assert await publish_source_card(cfg, factory, client, key)
            assert await notes(factory) == []
            await archive_source(cfg, factory, http, "synthetic", key)
            note, = await notes(factory)
            assert note.issue_number == 101 and note.transcription_state == "received"
            assert note.github_received_at is None and world.provider_calls == 0

    asyncio.run(run())


def test_lost_ack_absence_never_authorizes_another_issue_then_reconciles(tmp_path, monkeypatch):
    world = EarlyWorld()
    world.fail_post = "lost_ack"
    world.hide_receipts = True
    world.patches(monkeypatch)

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            for _ in range(3):
                await worker.process_watch_voice(early_settings(), factory, http)
            note, = await notes(factory)
            assert note.transcription_state == "transcribed"
            assert note.issue_number is None and note.next_part == 0
            assert world.posts == 1 and len(world.issues) == 1 and not world.comments
            async with factory() as session:
                row = await session.get(WatchVoiceSource, note.id)
                assert row.card_state == "unknown"
            world.hide_receipts = False
            await worker.process_watch_voice(early_settings(), factory, http)
            note, = await notes(factory)
            assert note.delivery_state == "delivered" and note.issue_number == 101
            assert len(world.issues) == 1 and len(world.comments) == 1
            assert world.provider_calls == 1

    asyncio.run(run())


def test_concurrent_publish_consumes_one_claim_and_fences_late_receipts(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def post(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return GithubReceipt(101, "https://github.com/markmalina/mark/issues/101",
                             datetime.now(UTC))

    async def reconcile(**kwargs):
        return None

    async def run():
        async with database(tmp_path) as factory:
            cfg = early_settings()
            key = await discover(cfg, factory, World().entry, None)
            client = SimpleNamespace(writer="synthetic-writer", post=post, reconcile=reconcile)
            publisher = asyncio.create_task(publish_source_card(cfg, factory, client, key))
            await entered.wait()
            async with factory() as session:
                claimed = await session.get(WatchVoiceSource, key)
                stale = copy.copy(claimed)
            assert not await publish_source_card(cfg, factory, client, key)
            release.set()
            assert await publisher
            assert not await publish_source_card(cfg, factory, client, key)
            assert len(calls) == 1
            stale.card_claim_id = "foreign-claim"
            receipt = GithubReceipt(999, "https://github.com/markmalina/mark/issues/999",
                                    datetime.now(UTC))
            with pytest.raises(WatchVoiceError, match="receipt_conflict"):
                await save_card_receipt(factory, stale, receipt)
            async with factory() as session:
                saved = await session.get(WatchVoiceSource, key)
                assert saved.card_issue_number == 101

    asyncio.run(run())


def test_unknown_card_reconciles_original_writer_after_driver_switch(tmp_path):
    world = EarlyWorld()
    world.fail_post = "lost_ack"

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            cfg = early_settings()
            key = await discover(cfg, factory, world.entry, None)
            first = MarkInboxClient(http, "synthetic", "synthetic-writer")
            assert not await publish_source_card(cfg, factory, first, key)
            second = MarkInboxClient(http, "synthetic-other-token", "new-driver-writer")
            assert await publish_source_card(cfg, factory, second, key)
            assert world.posts == 1

    asyncio.run(run())


def test_no_stt_key_still_creates_card_and_preserves_original(tmp_path, monkeypatch):
    world = EarlyWorld()
    world.patches(monkeypatch)

    async def run():
        from pydantic import SecretStr

        cfg = early_settings().model_copy(update={"openai_api_key": SecretStr("")})
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            await worker.process_watch_voice(cfg, factory, http)
            note, = await notes(factory)
            assert note.data == world.audio and note.issue_number == 101
            assert note.transcription_state == "received" and note.attempted_at is None
            assert note.last_error == "watch_voice_transcription_key_missing"
            assert len(world.issues) == 1 and not world.comments and world.provider_calls == 0

    asyncio.run(run())


def test_unknown_text_reconciles_original_writer_after_driver_switch(tmp_path, monkeypatch):
    world = EarlyWorld()
    world.patches(monkeypatch)

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            cfg = early_settings()
            key = await discover(cfg, factory, world.entry, None)
            first = MarkInboxClient(http, "synthetic", "synthetic-writer")
            await publish_source_card(cfg, factory, first, key)
            await archive_source(cfg, factory, http, "synthetic", key)
            await worker.transcribe_note(cfg, factory, http, key)
            note, = await notes(factory)
            world.fail_post = "lost_ack"
            await worker.publish_note(factory, first, note)
            note, = await notes(factory)
            assert note.delivery_state == "unknown"
            assert note.delivery_writer == "synthetic-writer"
            second = MarkInboxClient(http, "synthetic-new-token", "new-driver-writer")
            await worker.publish_note(factory, second, note)
            note, = await notes(factory)
            assert note.delivery_state == "delivered"
            assert len(world.comments) == 1 and world.posts == 2 and world.provider_calls == 1

    asyncio.run(run())


def test_default_off_and_legacy_note_do_not_backfill_metadata_card(tmp_path):
    assert not settings().gate_watch_voice_early_inbox_enabled

    async def run():
        async with database(tmp_path) as factory:
            cfg, world = early_settings(), World()
            key = await discover(settings(), factory, world.entry, None)
            await discover(cfg, factory, world.entry, None)
            async with factory() as session:
                row = await session.get(WatchVoiceSource, key)
                assert row.card_state == "legacy"
            another = {**world.entry, "id": "id:old"}
            broad = cfg.model_copy(update={"gate_watch_voice_file_id": ""})
            from app.infrastructure.watch_voice_sources import source_id
            old = queued_note(world, "id:old", datetime.now(UTC), revision=another["rev"])
            old.id = source_id(broad, another["id"], another["rev"])
            async with factory() as session:
                session.add(old)
                await session.commit()
            key = await discover(broad, factory, another, None)
            async with factory() as session:
                assert (await session.get(WatchVoiceSource, key)).card_state == "legacy"

    asyncio.run(run())


def test_pending_card_is_not_starved_by_fifty_older_unknown_results(tmp_path):
    created = []

    async def post(**kwargs):
        created.append(kwargs)
        return GithubReceipt(101, "https://github.com/markmalina/mark/issues/101",
                             datetime.now(UTC))

    async def reconcile(**kwargs):
        return None

    async def run():
        async with database(tmp_path) as factory:
            cfg = early_settings().model_copy(update={"gate_watch_voice_file_id": ""})
            for i in range(50):
                await discover(cfg, factory, {**World().entry, "id": f"id:old-{i}"}, None)
            async with factory() as session:
                old = list((await session.scalars(select(WatchVoiceSource))).all())
                from app.infrastructure.watch_voice_cards import card_body
                from app.infrastructure.watch_voice_github import body_digest
                for row in old:
                    row.card_state, row.card_writer = "unknown", "synthetic-writer"
                    row.card_body = card_body(row)
                    row.card_body_sha256 = body_digest(row.card_body)
                    row.card_claim_id = row.id
                    row.detected_at = datetime.now(UTC) - timedelta(hours=1)
                await session.commit()
            await discover(cfg, factory, World().entry, None)
            client = SimpleNamespace(writer="synthetic-writer", post=post, reconcile=reconcile)
            assert await drain_cards(cfg, factory, client) == 1
            assert len(created) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["reject", "wrong_writer", "changed_body"])
def test_definite_rejection_and_forged_reconciliation_cannot_create_duplicate(tmp_path, failure):
    world = EarlyWorld()
    world.fail_post = "reject" if failure == "reject" else "lost_ack"

    async def run():
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http):
            cfg = early_settings()
            key = await discover(cfg, factory, world.entry, None)
            client = MarkInboxClient(http, "synthetic", "synthetic-writer")
            assert not await publish_source_card(cfg, factory, client, key)
            if failure == "wrong_writer":
                world.issues[0]["user"]["login"] = "foreign-writer"
            elif failure == "changed_body":
                world.issues[0]["body"] += " changed"
            assert not await publish_source_card(cfg, factory, client, key)
            assert world.posts == 1
            async with factory() as session:
                source = await session.get(WatchVoiceSource, key)
                assert source.card_state == ("blocked" if failure == "reject" else "unknown")

    asyncio.run(run())


def test_actions_driver_early_card_and_text_share_issue(tmp_path, monkeypatch):
    world = ActionsWorld()
    world.patches(monkeypatch)
    original = world.handle

    def ordered(request):
        if request.url.path.endswith("/files/download"):
            assert len(world.issues) == 1 and world.provider_calls == 0
        return original(request)

    async def run():
        cfg = action_settings().model_copy(update={"gate_watch_voice_early_inbox_enabled": True})
        async with (database(tmp_path) as factory,
                    httpx.AsyncClient(transport=httpx.MockTransport(ordered)) as http):
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            await drain(settings=cfg, factory=factory, http=http,
                        claims=claims(), job_token="synthetic-token")
            note, = await notes(factory)
            assert note.delivery_state == "delivered" and note.next_part == 1
            assert len(world.issues) == len(world.comments) == world.provider_calls == 1
            assert world.transcript in world.comments[0]["body"]
            assert marker(note.id, 1) in world.comments[0]["body"]

    asyncio.run(run())


def test_additive_migration_preserves_legacy_and_refuses_to_drop_active_receipts():
    path = Path("alembic/versions/0117_watch_voice_early_inbox.py")
    spec = importlib.util.spec_from_file_location("early_card_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0116_watch_voice_observability"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        # An exact old journal shape is obtained by omitting only new columns.
        connection.exec_driver_sql("CREATE TABLE watch_voice_sources "
                                   "(id VARCHAR(36) PRIMARY KEY, filename VARCHAR(255), "
                                   "workspace_key VARCHAR(128), account_id VARCHAR(255))")
        connection.exec_driver_sql("INSERT INTO watch_voice_sources "
                                   "(id,filename) VALUES ('old', 'original.m4a')")
        connection.exec_driver_sql("CREATE TABLE watch_voice_notes "
                                   "(id VARCHAR(36) PRIMARY KEY, data BLOB, transcript TEXT)")
        connection.exec_driver_sql("INSERT INTO watch_voice_notes VALUES ('old', ?, ?)",
                                   (b"original\x00bytes", "полный старый текст"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        assert connection.exec_driver_sql(
            "SELECT id,filename,card_state FROM watch_voice_sources"
        ).one() == ("old", "original.m4a", "legacy")
        connection.exec_driver_sql("UPDATE watch_voice_sources SET card_state='unknown'")
        with Operations.context(context), pytest.raises(RuntimeError, match="lose_receipts"):
            migration.downgrade()
        connection.exec_driver_sql("UPDATE watch_voice_sources SET card_state='legacy'")
        with Operations.context(context):
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT id,filename FROM watch_voice_sources").one() == (
            "old", "original.m4a"
        )
        assert connection.exec_driver_sql("SELECT * FROM watch_voice_notes").one() == (
            "old", b"original\x00bytes", "полный старый текст"
        )
    engine.dispose()
