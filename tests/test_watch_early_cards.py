"""Exercise stage-one intake without a provider, audio download or network."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from app.infrastructure import watch_voice_early_cards as early
from app.infrastructure.watch_voice_github import GithubReceipt, WatchVoiceError
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_replay_and_lost_ack_reconcile_without_second_post(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(WatchEarlyCursor.__table__.create)
            await connection.run_sync(WatchEarlyCard.__table__.create)
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
