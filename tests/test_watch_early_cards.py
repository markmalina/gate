"""Exercise stage-one intake without a provider, audio download or network."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from app.infrastructure import watch_voice_early_cards as early
from app.infrastructure.watch_voice_github import GithubReceipt, WatchVoiceError
from app.models.watch_voice import (
    WatchEarlyCard,
    WatchEarlyCursor,
    WatchEarlyRejection,
    WatchVoiceNote,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_replay_and_lost_ack_reconcile_without_second_post(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(WatchEarlyCursor.__table__.create)
            await connection.run_sync(WatchEarlyCard.__table__.create)
            await connection.run_sync(WatchVoiceNote.__table__.create)
            await connection.run_sync(WatchEarlyRejection.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
        monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))
        modified = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        item = {
            ".tag": "file",
            "path_display": "/Alpha/Voice/test.m4a",
            "name": "test.m4a",
            "id": "id:test",
            "rev": "abc",
            "size": 123,
            "server_modified": modified,
        }
        calls = []

        def handler(request):
            calls.append(str(request.url))
            assert request.url.path in ("/2/files/list_folder", "/2/files/list_folder/continue")
            return httpx.Response(
                200, json={"entries": [item], "cursor": "cursor", "has_more": False}
            )

        class Client:
            posts = 0
            body = None

            async def post(self, *, part, issue_number, title, body):
                self.posts += 1
                self.body = body
                raise WatchVoiceError("lost_ack", unknown=True)

            async def reconcile(self, *, part, issue_number, body):
                assert body == self.body
                return GithubReceipt(
                    42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                )

        client = Client()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            first = await early.run_early_cards(settings, factory, http, client)
            second = await early.run_early_cards(settings, factory, http, client)
            third = await early.run_early_cards(settings, factory, http, client)
        async with factory() as session:
            card = await session.scalar(select(WatchEarlyCard))
            count = await session.scalar(select(func.count()).select_from(WatchEarlyCard))
            assert (card.state, card.issue_number, count) == ("saved", 42, 1)
        assert client.posts == 1
        assert first["considered"] == 1 and second["reconciled"] == 1 and third["result"] == "idle"
        # Each tick gives discovery its own pass, even when a receipt needs reconciliation.
        assert len(calls) == 3
        await engine.dispose()

    asyncio.run(scenario())


async def _return(value):
    return value


def test_cutover_preserves_files_in_the_same_second():
    cutover = datetime(2026, 11, 14, 15, 0, 0, 500000, tzinfo=UTC)
    assert early.after_cutover(datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC), cutover)
    assert not early.after_cutover(datetime(2026, 11, 14, 14, 59, 59, tzinfo=UTC), cutover)


def test_metadata_filters_and_oversized_audio_is_still_reported():
    entry = {
        ".tag": "file",
        "path_display": "/Alpha/Voice/x.m4a",
        "name": "x.m4a",
        "id": "id:a",
        "rev": "123",
        "size": 100_000_000,
        "server_modified": "2026-11-14T10:00:00Z",
    }
    assert early.metadata(entry) == entry
    assert early.metadata({**entry, "path_display": "/elsewhere/x.m4a"}) is None
    assert early.metadata({**entry, "name": "x.txt"}) is None


def test_actions_selects_metadata_only_and_never_calls_stt(monkeypatch):
    from app.infrastructure import watch_voice_actions as actions
    from app.worker import watch_voice

    async def fake_preflight(*args):
        return {"scope": "future-only"}

    async def fake_bound(settings, *args, **kwargs):
        return settings

    async def fake_run(*args):
        return {"result": "metadata-only"}

    async def forbidden(*args, **kwargs):
        raise AssertionError("transcription must not be called")

    monkeypatch.setattr(actions.ActionsInboxClient, "preflight_actions", fake_preflight)
    monkeypatch.setattr(actions, "bound_settings", fake_bound)
    monkeypatch.setattr(actions, "claim_run", lambda *_: _return(True))
    monkeypatch.setattr(early, "run_early_cards", fake_run)
    monkeypatch.setattr(watch_voice, "transcribe_note", forbidden)
    settings = SimpleNamespace(
        gate_watch_voice_w1_enabled=False,
        gate_watch_voice_early_cards_only=True,
        gate_watch_voice_actions_workflow_sha256="a" * 64,
    )
    result = asyncio.run(
        actions.drain(settings=settings, factory=None, http=None, claims={}, job_token="")
    )
    assert result == {"result": "metadata-only"}


def test_postgres_concurrent_workers_do_not_create_two_cards(tmp_path, monkeypatch):
    """Reproduce the overlap between an in-flight create and a second scan."""
    import os

    import pytest

    from test_watch_voice import database

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("requires PostgreSQL for row-lock semantics")

    async def scenario():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(lambda s: WatchEarlyCard.__table__.create(s.connection()))
                await session.run_sync(
                    lambda s: WatchEarlyRejection.__table__.create(s.connection())
                )
                await session.commit()
            settings = SimpleNamespace(
                gate_workspace_id="mark-personal", dropbox_call_account_id="synthetic-owner"
            )
            monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
            monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))
            item = {
                ".tag": "file",
                "path_display": "/Alpha/Voice/test.m4a",
                "name": "test.m4a",
                "id": "id:test",
                "rev": "abc",
                "size": 123,
                "server_modified": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            }
            entered, release = asyncio.Event(), asyncio.Event()

            class Client:
                posts = 0

                async def post(self, *, part, issue_number, title, body):
                    self.posts += 1
                    entered.set()
                    await release.wait()
                    return GithubReceipt(
                        42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                    )

                async def reconcile(self, *, part, issue_number, body):
                    return None

            def handler(request):
                return httpx.Response(
                    200, json={"entries": [item], "cursor": "cursor", "has_more": False}
                )

            client = Client()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                first = asyncio.create_task(early.run_early_cards(settings, factory, http, client))
                await asyncio.wait_for(entered.wait(), 5)
                second = await asyncio.wait_for(
                    early.run_early_cards(settings, factory, http, client), 5
                )
                assert second["result"] == "deferred"
                release.set()
                await asyncio.wait_for(first, 5)
            async with factory() as session:
                cards = (await session.scalars(select(WatchEarlyCard))).all()
                assert len(cards) == 1 and cards[0].state == "saved" and cards[0].issue_number == 42
            assert client.posts == 1

    asyncio.run(scenario())


def test_bad_metadata_does_not_block_valid_new_recording(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(WatchEarlyCursor.__table__.create)
            await connection.run_sync(WatchEarlyCard.__table__.create)
            await connection.run_sync(WatchVoiceNote.__table__.create)
            await connection.run_sync(WatchEarlyRejection.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
        monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))
        item = {
            ".tag": "file",
            "path_display": "/Alpha/Voice/test.m4a",
            "name": "test.m4a",
            "id": "id:test",
            "rev": "abc",
            "size": 123,
            "server_modified": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        old_bad = {**item, "id": "id:old", "size": 0, "server_modified": "2020-01-01T00:00:00Z"}
        new_bad = {**item, "id": "id:bad", "size": -1}
        no_name = {
            key: value for key, value in {**item, "id": "id:noname"}.items() if key != "name"
        }
        no_path = {
            key: value
            for key, value in {**item, "id": "id:nopath"}.items()
            if key != "path_display"
        }
        bad_time = {**item, "id": "id:badtime", "server_modified": "9999-12-31T23:59:59-01:00"}

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "entries": [old_bad, new_bad, no_name, no_path, bad_time, None, item],
                    "cursor": "cursor",
                    "has_more": False,
                },
            )

        class Client:
            async def post(self, **kwargs):
                return GithubReceipt(
                    42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            receipt = await early.run_early_cards(settings, factory, http, Client())
            again = await early.run_early_cards(settings, factory, http, Client())
        async with factory() as session:
            cards = (await session.scalars(select(WatchEarlyCard))).all()
            assert len(cards) == 1 and cards[0].file_id == "id:test" and cards[0].state == "saved"
            rejections = (await session.scalars(select(WatchEarlyRejection))).all()
            assert len(rejections) == 6
            assert sorted(r.reason for r in rejections) == sorted(
                [
                    "watch_early_size_invalid",
                    "watch_early_size_invalid",
                    "watch_early_name_or_path_missing",
                    "watch_early_name_or_path_missing",
                    "watch_early_time_invalid",
                    "watch_early_entry_unclassified",
                ]
            )
            assert all(len(r.metadata_sha256) == 64 for r in rejections)
        assert receipt["published_parts"] == 1
        assert again["result"] == "idle"
        await engine.dispose()

    asyncio.run(scenario())


def test_incomplete_and_overflow_metadata_are_explicitly_rejected():
    import pytest

    item = {
        ".tag": "file",
        "path_display": "/Alpha/Voice/test.m4a",
        "name": "test.m4a",
        "id": "id:test",
        "rev": "abc",
        "size": 123,
        "server_modified": "2026-11-14T16:00:00Z",
    }
    for field in ("name", "path_display"):
        invalid = {key: value for key, value in item.items() if key != field}
        with pytest.raises(WatchVoiceError):
            early.metadata(invalid)
    with pytest.raises(WatchVoiceError):
        early.metadata({**item, "server_modified": "9999-12-31T23:59:59-01:00"})
    with pytest.raises(WatchVoiceError):
        early.metadata({key: value for key, value in item.items() if key != ".tag"})
    with pytest.raises(WatchVoiceError):
        early.metadata(None)
    assert early.metadata({".tag": "folder"}) is None
    assert early.metadata({**item, "name": "notes.txt"}) is None


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_pending_card_is_delivered_without_dropbox(monkeypatch, state):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchVoiceNote.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        now = datetime.now(UTC)
        entry = {
            ".tag": "file",
            "path_display": "/Alpha/Voice/test.m4a",
            "name": "test.m4a",
            "id": "id:test",
            "rev": "abc",
            "size": 123,
            "server_modified": now.isoformat(),
        }
        async with factory() as session:
            session.add(
                WatchEarlyCursor(
                    workspace_key="mark-personal", account_id="test", cursor="saved", cutover_at=now
                )
            )
            values = early.card_values(settings, entry, now)
            values["state"] = state
            session.add(WatchEarlyCard(**values))
            await session.commit()

        async def unavailable(*args):
            raise WatchVoiceError("dropbox_unavailable")

        monkeypatch.setattr(early, "_refresh_access_token", unavailable)

        class Client:
            async def post(self, **kwargs):
                assert state == "pending"
                return GithubReceipt(
                    42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                )

            async def reconcile(self, **kwargs):
                assert state == "unknown"
                return GithubReceipt(
                    42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                )

        async with httpx.AsyncClient(trust_env=False) as http:
            receipt = await early.run_early_cards(settings, factory, http, Client())
        async with factory() as session:
            card = await session.scalar(select(WatchEarlyCard))
            assert card.state == "saved" and card.issue_number == 42
        assert receipt["published_parts"] == (state == "pending")
        assert receipt["reconciled"] == (state == "unknown")
        await engine.dispose()

    asyncio.run(scenario())


def test_missing_cursor_with_existing_cards_fails_closed():
    import pytest

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchVoiceNote.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        now = datetime.now(UTC)
        entry = {
            ".tag": "file",
            "path_display": "/Alpha/Voice/test.m4a",
            "name": "test.m4a",
            "id": "id:test",
            "rev": "abc",
            "size": 123,
            "server_modified": now.isoformat(),
        }
        async with factory() as session:
            session.add(WatchEarlyCard(**early.card_values(settings, entry, now)))
            await session.commit()
        async with httpx.AsyncClient(trust_env=False) as http:
            with pytest.raises(WatchVoiceError, match="watch_early_cursor_missing_with_cards"):
                await early.run_early_cards(settings, factory, http, object())
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(WatchEarlyCursor)) == 0
        await engine.dispose()

    asyncio.run(scenario())


def test_unknown_reconciliation_failure_does_not_block_new_discovery(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchVoiceNote.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        now = datetime.now(UTC)
        entry = {
            ".tag": "file",
            "path_display": "/Alpha/Voice/test.m4a",
            "name": "test.m4a",
            "id": "id:old",
            "rev": "abc",
            "size": 123,
            "server_modified": (now + timedelta(minutes=1)).isoformat(),
        }
        async with factory() as session:
            session.add(
                WatchEarlyCursor(
                    workspace_key="mark-personal", account_id="test", cursor="saved", cutover_at=now
                )
            )
            values = early.card_values(settings, entry, now)
            values["state"] = "unknown"
            session.add(WatchEarlyCard(**values))
            await session.commit()
        monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
        monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))

        class Client:
            async def reconcile(self, **kwargs):
                raise WatchVoiceError("github_unavailable", unknown=True)

            async def post(self, **kwargs):
                return GithubReceipt(
                    42, "https://github.com/markmalina/mark/issues/42", datetime.now(UTC)
                )

        def handler(request):
            return httpx.Response(
                200,
                json={"entries": [{**entry, "id": "id:new"}], "cursor": "new", "has_more": False},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            first = await early.run_early_cards(settings, factory, http, Client())
            second = await early.run_early_cards(settings, factory, http, Client())
        async with factory() as session:
            cards = (await session.scalars(select(WatchEarlyCard))).all()
            assert {card.state for card in cards} == {"unknown", "saved"}
        assert first["published_parts"] == 1
        assert second["published_parts"] == 0
        await engine.dispose()

    asyncio.run(scenario())


def test_batch_publishes_up_to_five_and_preserves_remaining_queue(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchVoiceNote.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal",
            dropbox_call_account_id="test",
            gate_watch_voice_early_batch_size=5,
        )
        monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
        monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))
        modified = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        entries = [
            {
                ".tag": "file",
                "path_display": f"/Alpha/Voice/{i}.m4a",
                "name": f"{i}.m4a",
                "id": f"id:file{i}",
                "rev": "abc",
                "size": 123,
                "server_modified": modified,
            }
            for i in range(7)
        ]

        def handler(request):
            return httpx.Response(
                200, json={"entries": entries, "cursor": "next", "has_more": False}
            )

        class Client:
            posts = 0

            async def post(self, **kwargs):
                self.posts += 1
                return GithubReceipt(
                    self.posts,
                    f"https://github.com/markmalina/mark/issues/{self.posts}",
                    datetime.now(UTC),
                )

        client = Client()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            first = await early.run_early_cards(settings, factory, http, client)
            second = await early.run_early_cards(settings, factory, http, client)
            third = await early.run_early_cards(settings, factory, http, client)
        assert (first["considered"], first["published_parts"]) == (5, 5)
        assert (second["considered"], second["published_parts"]) == (2, 2)
        assert third["result"] == "idle" and client.posts == 7
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(WatchEarlyCard)) == 7
        await engine.dispose()

    asyncio.run(scenario())


def test_batch_drains_saved_queue_when_dropbox_is_unavailable(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchVoiceNote.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal",
            dropbox_call_account_id="test",
            gate_watch_voice_early_batch_size=5,
        )
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(
                WatchEarlyCursor(
                    workspace_key="mark-personal", account_id="test", cursor="saved", cutover_at=now
                )
            )
            for i in range(3):
                entry = {
                    ".tag": "file",
                    "path_display": f"/Alpha/Voice/{i}.m4a",
                    "name": f"{i}.m4a",
                    "id": f"id:file{i}",
                    "rev": "abc",
                    "size": 123,
                    "server_modified": now.isoformat(),
                }
                session.add(WatchEarlyCard(**early.card_values(settings, entry, now)))
            await session.commit()

        async def unavailable(*args):
            raise WatchVoiceError("dropbox_unavailable")

        monkeypatch.setattr(early, "_refresh_access_token", unavailable)

        class Client:
            posts = 0

            async def post(self, **kwargs):
                self.posts += 1
                return GithubReceipt(
                    self.posts,
                    f"https://github.com/markmalina/mark/issues/{self.posts}",
                    datetime.now(UTC),
                )

        client = Client()
        async with httpx.AsyncClient(trust_env=False) as http:
            receipt = await early.run_early_cards(settings, factory, http, client)
        assert receipt["published_parts"] == 3 and receipt["considered"] == 3
        assert client.posts == 3
        await engine.dispose()

    asyncio.run(scenario())


def test_page_commits_before_next_provider_failure(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            for table in (
                WatchEarlyCursor.__table__,
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
            ):
                await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = SimpleNamespace(
            gate_workspace_id="mark-personal", dropbox_call_account_id="test"
        )
        monkeypatch.setattr(early, "_refresh_access_token", lambda *_: _return("test"))
        monkeypatch.setattr(early, "_verify_account_id", lambda *_: _return(None))
        scope = early.watch_scope(settings)
        await early.ensure_early_cursor(factory, scope)
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 2:
                return httpx.Response(503, json={})
            entry = {
                ".tag": "file",
                "path_display": "/Alpha/Voice/test.m4a",
                "name": "test.m4a",
                "id": "id:test",
                "rev": "abc",
                "size": 123,
                "server_modified": (datetime.now(UTC) + timedelta(seconds=5)).isoformat(),
            }
            return httpx.Response(
                200, json={"entries": [entry], "cursor": "next-page", "has_more": True}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(WatchVoiceError, match="watch_early_delta_invalid"):
                await early.discover_early_cards(settings, factory, http, writer="legacy_actions")
            with pytest.raises(WatchVoiceError, match="watch_early_writer_inactive"):
                await early.discover_early_cards(settings, factory, http, writer="app")
        async with factory() as session:
            cursor = await session.get(WatchEarlyCursor, ("mark-personal", "test"))
            assert cursor.cursor == "next-page"
            assert (await session.scalar(select(WatchEarlyCard))).state == "pending"
        assert len(calls) == 2
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_state", ["delivered", "unknown"])
def test_early_card_respects_existing_legacy_binding(tmp_path, legacy_state):
    from test_watch_voice import database

    async def scenario():
        async with database(tmp_path) as factory:
            settings = SimpleNamespace(
                gate_workspace_id="mark-personal", dropbox_call_account_id="test"
            )
            now = datetime.now(UTC)
            values = early.card_values(
                settings, {"id": "id:legacy", "rev": "abc", "server_modified": now.isoformat()}, now
            )
            async with factory() as session:
                await session.run_sync(lambda s: WatchEarlyCard.__table__.create(s.connection()))
                session.add(
                    WatchEarlyCursor(
                        workspace_key="mark-personal",
                        account_id="test",
                        writer_mode="app",
                        cutover_at=now,
                    )
                )
                session.add(WatchEarlyCard(**values))
                session.add(
                    WatchVoiceNote(
                        id=values["id"],
                        workspace_key="mark-personal",
                        account_id="test",
                        file_id="id:legacy",
                        revision="abc",
                        source_path="/Alpha/Voice/test.m4a",
                        filename="test.m4a",
                        media_type="audio/mp4",
                        size_bytes=1,
                        sha256="0" * 64,
                        data=b"x",
                        dropbox_modified_at=now,
                        received_at=now,
                        delivery_state=legacy_state,
                        issue_url="https://github.com/markmalina/mark/issues/42",
                    )
                )
                await session.commit()

            class Client:
                async def post(self, **kwargs):
                    raise AssertionError("must not create a second issue")

            key = early.watch_scope(settings)
            assert (await early.publish_saved(factory, Client(), key))["result"] == "deferred"
            await early.publish_saved(factory, Client(), key, writer="app")
            async with factory() as session:
                card = await session.get(WatchEarlyCard, values["id"])
                if legacy_state == "delivered":
                    assert (card.state, card.issue_number) == ("saved", 42)
                else:
                    assert (card.state, card.last_error) == (
                        "blocked",
                        "watch_early_legacy_receipt_required",
                    )

    asyncio.run(scenario())
