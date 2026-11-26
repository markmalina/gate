"""Real drain/SQLAlchemy claims/outbox; synthetic HTTP and STT, SQLite unless PG supplied.

SQLite exercises CAS/replay and persistence, not PostgreSQL coordinator races.
"""

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure import watch_voice_actions as actions
from app.infrastructure import watch_voice_w1 as w1
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.watch_voice import WatchVoiceCursor, WatchVoiceDrainRun, WatchVoiceNote
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test_watch_voice import database, notes
from test_watch_voice_actions import ActionsWorld, action_settings, claims

GATE_SHA = "b" * 40  # Deliberately different from the synthetic private Mark SHA.
CURSOR_KEY = ("mark-personal", "synthetic-owner")


def row(world, label, when, **extra):
    return WatchVoiceNote(
        **{
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-w1:" + label)),
            "workspace_key": CURSOR_KEY[0],
            "account_id": CURSOR_KEY[1],
            "file_id": "id:" + label,
            "revision": "abcdef1234",
            "source_path": "/Alpha/Voice/Synthetic.m4a",
            "filename": "Synthetic.m4a",
            "media_type": "audio/mp4",
            "size_bytes": len(world.audio),
            "sha256": hashlib.sha256(world.audio).hexdigest(),
            "data": world.audio,
            "duration_seconds": 5,
            "dropbox_modified_at": when,
            "received_at": when,
            "transcription_state": "received",
            "delivery_state": "pending",
            "next_part": 0,
            **extra,
        }
    )


def configured(target, **changes):
    now = datetime.now(UTC)
    admission = {
        "schema": "watch-voice-w1-admission.v1",
        "event_id": target.id,
        "workspace_key": target.workspace_key,
        "account_id": target.account_id,
        "file_id": target.file_id,
        "revision": target.revision,
        "sha256": target.sha256,
        "gate_sha": GATE_SHA,
        "not_before": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        **changes,
    }
    return action_settings().model_copy(
        update={
            "deployment_sha": GATE_SHA,
            "gate_watch_voice_w1_enabled": True,
            "gate_watch_voice_w1_admission": SecretStr(json.dumps(admission)),
        }
    )


@asynccontextmanager
async def setup(tmp_path, monkeypatch, **target_changes):
    world = ActionsWorld()
    world.patches(monkeypatch)
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        return world.handle(request)

    async with (
        database(tmp_path) as factory,
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http,
    ):
        now = datetime.now(UTC)
        cutover = now - timedelta(minutes=10)
        initial = row(
            world,
            "initial",
            cutover - timedelta(minutes=1),
            transcription_state="transcribed",
            transcript="synthetic initial",
            delivery_state="delivered",
            issue_number=100,
            issue_url="https://github.com/markmalina/mark/issues/100",
        )
        target = row(world, "target", now - timedelta(minutes=1), **target_changes)
        older = row(world, "older", cutover + timedelta(minutes=1))
        stale = row(
            world,
            "stale",
            cutover + timedelta(minutes=2),
            transcription_state="processing",
            attempted_at=now - timedelta(days=1),
        )
        world.binding = {
            "schema": "watch-voice-binding.v1",
            "enabled": True,
            "workspace_key": "mark-personal",
            "scope": "future-only",
            "accepted_initial_event_id": initial.id,
        }
        async with factory() as session:
            await session.run_sync(lambda s: WatchVoiceDrainRun.__table__.create(s.connection()))
            session.add(
                WatchVoiceCursor(
                    workspace_key=CURSOR_KEY[0],
                    account_id=CURSOR_KEY[1],
                    cursor="preserved",
                    future_cutover_at=cutover,
                )
            )
            session.add_all([initial, older, stale, target])
            await session.commit()
        yield SimpleNamespace(
            world=world,
            http=http,
            factory=factory,
            target=target,
            settings=configured(target),
            calls=calls,
        )


async def invoke(env, index=0, **changes):
    env.world.current_run = str(321 + index)
    return await actions.drain(
        settings=env.settings,
        factory=env.factory,
        http=env.http,
        claims=claims(
            **{
                "run_id": env.world.current_run,
                "jti": "synthetic-w1-jti-" + str(index),
                **changes,
            }
        ),
        job_token="synthetic-job-token",
    )


async def snapshot(env):
    rows = await notes(env.factory)
    async with env.factory() as session:
        cursor = await session.get(WatchVoiceCursor, CURSOR_KEY)
        cursor_value = (cursor.cursor, cursor.future_cutover_at) if cursor else None
    return {
        note.id: {
            column.key: getattr(note, column.key) for column in WatchVoiceNote.__table__.columns
        }
        for note in rows
        if note.id != env.target.id
    }, cursor_value


def no_dropbox(env):
    assert not any("dropboxapi.com" in url for _, url in env.calls)


def test_select_target_not_older_and_restart_after_delivery(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            before = await snapshot(env)
            first = await invoke(env)
            assert first["delivered"] == first["published_parts"] == 1
            assert env.world.provider_calls == env.world.posts == 1
            rows = await notes(env.factory)
            assert next(n for n in rows if n.id == env.target.id).delivery_state == "delivered"
            for index in range(1, 4):
                assert (await invoke(env, index))["result"] == "idle"
            # Re-open persisted synthetic DB with a fresh engine and fresh Settings.
            original_engine = env.factory.kw["bind"]
            schema = None
            connect_args = {}
            if original_engine.dialect.name == "postgresql":
                async with original_engine.connect() as connection:
                    schema = await connection.scalar(text("SELECT current_schema()"))
                assert schema and schema.startswith("watch_test_")
                connect_args = {"server_settings": {"search_path": schema}}
            # A URL object retains the password; str(URL) replaces it with ***.
            engine = create_async_engine(original_engine.url, connect_args=connect_args)
            try:
                if schema is not None:
                    async with engine.connect() as connection:
                        assert await connection.scalar(text("SELECT current_schema()")) == schema
                env.factory = async_sessionmaker(engine, expire_on_commit=False)
                env.settings = Settings(
                    **{
                        (f.validation_alias if isinstance(f.validation_alias, str) else k): v
                        for k, f in Settings.model_fields.items()
                        for v in [env.settings.model_dump()[k]]
                    }
                )
                assert (await invoke(env, 4))["result"] == "idle"
                assert await snapshot(env) == before
            finally:
                await engine.dispose()
            no_dropbox(env)
            assert env.world.provider_calls == env.world.posts == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": str(uuid.UUID(int=7))},
        {"file_id": "id:other"},
        {"revision": "ffffff"},
        {"sha256": "f" * 64},
        {"workspace_key": "foreign"},
        {"account_id": "foreign"},
        {"gate_sha": "c" * 40},
        {"gate_sha": "unknown"},
        {"not_before": "2026-01-01"},
        {"expires_at": "2026-01-01T00:00:00Z"},
        {"not_before": "2099-01-01T00:00:00Z"},
        {"extra": "not-admitted"},
    ],
)
def test_bad_admission_never_scans_or_changes_other_notes(tmp_path, monkeypatch, change):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            before = await snapshot(env)
            env.settings = configured(env.target, **change)
            try:
                receipt = await invoke(env)
                assert receipt["result"] == "deferred"
            except WatchVoiceError as exc:
                assert "watch_voice_w1" in exc.code
                assert env.target.file_id not in str(exc)
            assert env.world.provider_calls == env.world.posts == 0
            assert await snapshot(env) == before
            no_dropbox(env)

    asyncio.run(run())


@pytest.mark.parametrize("raw", ["", "{}", "null", "not-json", '{"schema":"x","schema":"y"}'])
def test_missing_or_malformed_server_admission_is_closed(tmp_path, monkeypatch, raw):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            env.settings = env.settings.model_copy(
                update={
                    "gate_watch_voice_w1_admission": SecretStr(raw),
                }
            )
            with pytest.raises(WatchVoiceError, match="w1_admission_invalid"):
                await invoke(env)
            assert env.calls == []  # rejected even before private GitHub reads
            assert env.world.provider_calls == env.world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["initial-exact", "initial-quota-probe", "initial-post-payment"])
def test_old_recovery_binding_cannot_bypass_w1(tmp_path, monkeypatch, scope):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            monkeypatch.setattr(
                actions.ActionsInboxClient,
                "preflight_actions",
                AsyncMock(return_value={"scope": scope}),
            )
            recover = AsyncMock(side_effect=AssertionError("recovery is forbidden"))
            monkeypatch.setattr(actions, "recover_note", recover)
            monkeypatch.setattr(actions, "continue_note", recover)
            with pytest.raises(WatchVoiceError, match="w1_future_only_required"):
                await invoke(env)
            recover.assert_not_called()
            assert env.world.provider_calls == env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())


@pytest.mark.parametrize("state", ["processing", "unknown", "rejected"])
def test_provider_states_are_never_rearmed(tmp_path, monkeypatch, state):
    async def run():
        async with setup(
            tmp_path,
            monkeypatch,
            transcription_state=state,
            attempted_at=datetime.now(UTC) - timedelta(days=2),
        ) as env:
            before = await snapshot(env)
            for index in range(2):
                assert (await invoke(env, index))["result"] == "deferred"
            saved = next(n for n in await notes(env.factory) if n.id == env.target.id)
            assert saved.transcription_state == state
            assert env.world.provider_calls == env.world.posts == 0
            assert await snapshot(env) == before
            no_dropbox(env)

    asyncio.run(run())


def test_transcribed_pending_continues_same_issue_without_stt(tmp_path, monkeypatch):
    async def run():
        async with setup(
            tmp_path,
            monkeypatch,
            transcription_state="transcribed",
            transcript="x" * 24001,
            next_part=1,
            issue_number=101,
            issue_url="https://github.com/markmalina/mark/issues/101",
        ) as env:
            receipt = await invoke(env)
            assert receipt["delivered"] == receipt["published_parts"] == 1
            assert env.world.provider_calls == 0 and env.world.posts == 1
            assert not env.world.issues and len(env.world.comments) == 1
            assert env.world.comments[0]["html_url"].startswith(
                "https://github.com/markmalina/mark/issues/101#"
            )
            no_dropbox(env)

    asyncio.run(run())


def test_publication_lost_ack_reconciles_and_never_posts_twice(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            env.world.fail_post = "lost_ack"
            assert (await invoke(env))["published_parts"] == 0
            receipt = await invoke(env, 1)
            assert receipt["reconciled"] == receipt["delivered"] == 1
            assert (await invoke(env, 2))["result"] == "idle"
            assert env.world.provider_calls == env.world.posts == 1
            no_dropbox(env)

    asyncio.run(run())


def test_publication_unknown_absence_is_not_permission_to_post(tmp_path, monkeypatch):
    async def run():
        async with setup(
            tmp_path,
            monkeypatch,
            transcription_state="transcribed",
            transcript="synthetic text",
            delivery_state="unknown",
        ) as env:
            for index in range(2):
                assert (await invoke(env, index))["reconciled"] == 0
            assert env.world.provider_calls == env.world.posts == 0
            assert (
                next(n for n in await notes(env.factory) if n.id == env.target.id).delivery_state
                == "unknown"
            )
            no_dropbox(env)

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"data": b"tampered"},
        {"data": b"x" * len(b"synthetic-original-audio")},
        {"duration_seconds": None},
        {"duration_seconds": 3601},
        {"attempted_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {"last_error": "synthetic_prior_attempt"},
        {"delivery_state": "unknown"},
        {"delivery_state": "sending"},
        {"delivery_state": "blocked"},
    ],
)
def test_unsafe_archive_or_claim_has_no_effects(tmp_path, monkeypatch, change):
    async def run():
        async with setup(tmp_path, monkeypatch, **change) as env:
            assert (await invoke(env))["result"] == "deferred"
            assert env.world.provider_calls == env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "target-absent",
        "empty-eligible",
        "missing-cutover",
        "pre-cutover",
    ],
)
def test_no_cursor_initialization_or_fallback(tmp_path, monkeypatch, case):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            async with env.factory() as session:
                if case in {"target-absent", "empty-eligible"}:
                    await session.delete(await session.get(WatchVoiceNote, env.target.id))
                    if case == "empty-eligible":
                        for item in await notes(env.factory):
                            if item.delivery_state != "delivered" and item.id != env.target.id:
                                await session.delete(await session.get(WatchVoiceNote, item.id))
                elif case == "missing-cutover":
                    cursor = await session.get(WatchVoiceCursor, CURSOR_KEY)
                    cursor.future_cutover_at = None
                else:
                    target = await session.get(WatchVoiceNote, env.target.id)
                    target.dropbox_modified_at = datetime(2000, 1, 1, tzinfo=UTC)
                await session.commit()
            before = await snapshot(env)
            assert (await invoke(env))["result"] == "deferred"
            assert await snapshot(env) == before
            assert env.world.provider_calls == env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())


def test_repeat_jti_and_concurrent_fresh_runs_keep_cas_barriers(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            before = await snapshot(env)
            # Stub only authenticated GitHub read: caller claims differ concurrently.
            binding = env.world.binding
            monkeypatch.setattr(
                actions.ActionsInboxClient, "preflight_actions", AsyncMock(return_value=binding)
            )
            await asyncio.gather(*(invoke(env, index) for index in range(4)))
            assert env.world.provider_calls == env.world.posts == 1
            assert (await invoke(env, 0))["result"] == "replayed"
            assert (await invoke(env, 4, jti="synthetic-w1-jti-0"))["result"] == "replayed"
            assert (await invoke(env, 5))["result"] == "idle"
            assert await snapshot(env) == before
            no_dropbox(env)

    asyncio.run(run())


def test_expiry_after_admitted_stt_does_not_start_publication(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            original = actions.worker.TelegramVoiceTranscriptionService

            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(UTC) + timedelta(days=1)

            class Delayed(original):
                async def transcribe(self, command):
                    result = await super().transcribe(command)
                    monkeypatch.setattr(w1, "datetime", Clock)
                    return result

            monkeypatch.setattr(actions.worker, "TelegramVoiceTranscriptionService", Delayed)
            assert (await invoke(env))["result"] == "deferred"
            assert env.world.provider_calls == 1 and env.world.posts == 0
            assert (
                next(
                    n for n in await notes(env.factory) if n.id == env.target.id
                ).transcription_state
                == "transcribed"
            )
            no_dropbox(env)

    asyncio.run(run())


def test_default_off_ignores_admission_and_retains_legacy_scan(tmp_path, monkeypatch):
    assert Settings().gate_watch_voice_w1_enabled is False
    assert Settings().gate_watch_voice_w1_admission.get_secret_value() == ""
    world = ActionsWorld()
    world.patches(monkeypatch)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http,
        ):
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            receipt = await actions.drain(
                settings=action_settings().model_copy(
                    update={
                        "gate_watch_voice_w1_admission": SecretStr("invalid but OFF"),
                    }
                ),
                factory=factory,
                http=http,
                claims=claims(),
                job_token="synthetic-job",
            )
            assert receipt["delivered"] == 1
            assert world.provider_calls == world.posts == 1
            assert any("dropboxapi.com" in url for _, url in world.calls)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "event_id",
        "workspace_key",
        "account_id",
        "file_id",
        "revision",
        "sha256",
        "gate_sha",
        "not_before",
        "expires_at",
    ],
)
def test_each_missing_admission_binding_is_closed(tmp_path, monkeypatch, field):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            obj = json.loads(env.settings.gate_watch_voice_w1_admission.get_secret_value())
            del obj[field]
            secret = json.dumps(obj)
            env.settings = env.settings.model_copy(
                update={
                    "gate_watch_voice_w1_admission": SecretStr(secret),
                }
            )
            assert secret not in repr(env.settings)
            with pytest.raises(WatchVoiceError, match="w1_admission_invalid"):
                await invoke(env)
            assert env.calls == []
            assert env.world.provider_calls == env.world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("event", ["schedule", "push", "workflow_dispatch", "issue_comment"])
def test_all_old_authenticated_callers_use_same_server_target(tmp_path, monkeypatch, event):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            env.world.run_event = event
            payload = await invoke(env, event_name=event, actor_id="23397294")
            assert payload["delivered"] == 1
            assert set(payload) == {
                "schema",
                "result",
                "considered",
                "published_parts",
                "delivered",
                "reconciled",
                "private_content_exposed",
            }
            assert payload["schema"] == "watch-voice-drain.v1"
            assert payload["private_content_exposed"] is False
            assert env.target.id not in json.dumps(payload)
            assert env.target.sha256 not in json.dumps(payload)
            assert env.world.provider_calls == env.world.posts == 1
            no_dropbox(env)

    asyncio.run(run())


@pytest.mark.parametrize("attribute", ["public_repo", "foreign_repo"])
def test_w1_retains_private_mark_preflight(tmp_path, monkeypatch, attribute):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            setattr(env.world, attribute, True)
            with pytest.raises(WatchVoiceError, match="private_target_mismatch"):
                await invoke(env)
            assert env.world.provider_calls == env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())


def test_started_claim_is_committed_before_synthetic_stt(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            original = actions.worker.TelegramVoiceTranscriptionService

            class Observed(original):
                async def transcribe(self, command):
                    async with env.factory() as session:
                        note = await session.get(WatchVoiceNote, env.target.id)
                        assert note.transcription_state == "processing"
                        assert note.attempted_at is not None
                    return await super().transcribe(command)

            monkeypatch.setattr(actions.worker, "TelegramVoiceTranscriptionService", Observed)
            assert (await invoke(env))["delivered"] == 1
            assert env.world.provider_calls == env.world.posts == 1
            no_dropbox(env)

    asyncio.run(run())


def test_new_provider_unknown_stays_unknown_on_new_run(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            env.world.transcription_error = True
            assert (await invoke(env))["result"] == "deferred"
            assert (await invoke(env, 1))["result"] == "deferred"
            note = next(n for n in await notes(env.factory) if n.id == env.target.id)
            assert note.transcription_state == "unknown" and note.attempted_at is not None
            assert env.world.provider_calls == 1 and env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())


def test_busy_coordinator_is_no_effect_not_scan(tmp_path, monkeypatch):
    async def run():
        async with setup(tmp_path, monkeypatch) as env:
            factory = env.factory

            class BusySession:
                def __init__(self):
                    self.session = factory()

                async def __aenter__(self):
                    await self.session.__aenter__()
                    return self

                async def __aexit__(self, *args):
                    return await self.session.__aexit__(*args)

                def __getattr__(self, name):
                    return getattr(self.session, name)

                async def scalar(self, _statement):
                    return None  # Simulates PostgreSQL SKIP LOCKED, not a PG proof.

            env.factory = BusySession
            assert (await invoke(env))["result"] == "deferred"
            assert env.world.provider_calls == env.world.posts == 0
            no_dropbox(env)

    asyncio.run(run())
