"""Synthetic private workflow credentials and audio; no network or real content."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.github_actions_oidc import GitHubOidcError
from app.core.watch_voice_actions_oidc import OWNER_ID, WORKFLOW_REF, authorize_claims
from app.infrastructure.watch_voice_actions import (
    BINDING_PATH,
    BOT_WRITER,
    ActionsInboxClient,
    bound_settings,
    claim_run,
    drain,
    validate_binding,
)
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.watch_voice import WatchVoiceCursor, WatchVoiceDrainRun, WatchVoiceNote
from sqlalchemy import select

from test_watch_voice import World, database, notes, queued_note, settings

WORKFLOW = b"synthetic-reviewed-workflow"
PIN = hashlib.sha256(WORKFLOW).hexdigest()
SHA = "a" * 40


def test_actions_rejects_persistent_driver_before_any_io(monkeypatch):
    def unexpected_client(*args, **kwargs):
        raise AssertionError("conflicting driver reached GitHub client")

    monkeypatch.setattr(
        "app.infrastructure.watch_voice_actions.ActionsInboxClient", unexpected_client
    )
    factory, http = AsyncMock(), AsyncMock()
    conflicting = settings().model_copy(update={"gate_watch_voice_actions_enabled": True})
    with pytest.raises(WatchVoiceError, match="^watch_voice_driver_conflict$"):
        asyncio.run(drain(
            settings=conflicting, factory=factory, http=http,
            claims={}, job_token="synthetic-unused-token",
        ))
    factory.assert_not_called()
    assert http.mock_calls == []


def claims(**extra):
    return {
        "repository": "markmalina/mark",
        "repository_id": "1336157811",
        "repository_owner_id": OWNER_ID,
        "repository_visibility": "private",
        "ref": "refs/heads/main",
        "workflow_ref": WORKFLOW_REF,
        "sub": "repo:markmalina/mark:ref:refs/heads/main",
        "runner_environment": "github-hosted",
        "run_id": "321",
        "run_attempt": "1",
        "event_name": "schedule",
        "sha": SHA,
        "jti": "synthetic-jwt-id",
        "iat": datetime.now(UTC).timestamp(),
        **extra,
    }


def action_settings():
    return settings().model_copy(
        update={
            "gate_watch_voice_enabled": False,
            "gate_watch_voice_actions_enabled": True,
            "gate_watch_voice_actions_workflow_sha256": PIN,
            "gate_mark_github_token": "",
            "gate_mark_github_writer": "",
        }
    )


class ActionsWorld(World):
    def __init__(self):
        super().__init__()
        self.entry["rev"] = "abcdef1234"
        self.binding = {
            "schema": "watch-voice-binding.v1",
            "enabled": True,
            "workspace_key": "mark-personal",
            "scope": "initial-exact",
            "file_id": "id:synthetic",
            "revision": "abcdef1234",
        }
        self.current_sha = SHA
        self.workflow = WORKFLOW
        self.run_event = "schedule"
        self.current_run = "321"

    def handle(self, request):
        path = request.url.path
        if request.method == "GET" and path == "/repos/markmalina/mark":
            return httpx.Response(
                200,
                json={
                    "full_name": "other/repo" if self.foreign_repo else "markmalina/mark",
                    "id": 1336157811,
                    "owner": {"id": 23397294, "login": "markmalina"},
                    "private": not self.public_repo,
                    "archived": False,
                    "has_issues": True,
                    "default_branch": "main",
                },
            )
        if path == "/repos/markmalina/mark/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": self.current_sha}})
        if path.startswith("/repos/markmalina/mark/actions/runs/"):
            return httpx.Response(
                200,
                json={
                    "id": int(self.current_run),
                    "head_sha": SHA,
                    "head_branch": "main",
                    "run_attempt": 1,
                    "path": ".github/workflows/gate-watch-voice-drain.yml",
                    "event": self.run_event,
                    "status": "in_progress",
                },
            )
        if path.startswith("/repos/markmalina/mark/contents/"):
            name = path.split("/contents/", 1)[1]
            data = json.dumps(self.binding).encode() if name == BINDING_PATH else self.workflow
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "path": name,
                    "size": len(data),
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode(),
                },
            )
        try:
            response = super().handle(request)
        except httpx.ReadTimeout:
            for receipt in self.issues + self.comments:
                receipt["user"] = {"login": BOT_WRITER}
            raise
        if request.method == "POST" and path.startswith("/repos/markmalina/mark/issues"):
            # Super stores this same dict in fixture journal before response construction.
            for receipt in self.issues + self.comments:
                receipt["user"] = {"login": BOT_WRITER}
            if response.status_code == 201:
                obj = response.json()
                obj["user"] = {"login": BOT_WRITER}
                return httpx.Response(201, json=obj)
        return response


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "foreign/repo"},
        {"repository_id": "1"},
        {"repository_owner_id": "1"},
        {"repository_visibility": "public"},
        {"ref": "refs/heads/other"},
        {"workflow_ref": "foreign/workflow"},
        {"sub": "repo:foreign:pull_request"},
        {"run_attempt": "2"},
        {"runner_environment": "self-hosted"},
        {"event_name": "pull_request"},
        {"event_name": "repository_dispatch"},
        {"run_id": "../other"},
        {"sha": "not-sha"},
        {"jti": ""},
        {"iat": 1},
        {"event_name": "push", "actor_id": "1"},
        {"event_name": "issue_comment", "actor_id": "1"},
        {"event_name": "issue_comment", "actor_id": None},
        {"event_name": "issue_comment", "actor_id": int(OWNER_ID)},
    ],
)
def test_oidc_rejects_wrong_exact_identity(change):
    with pytest.raises(GitHubOidcError):
        authorize_claims(claims(**change))


def test_oidc_accepts_exact_schedule_or_owner_main_push():
    authorize_claims(claims())
    authorize_claims(claims(event_name="push", actor_id=OWNER_ID))
    authorize_claims(claims(event_name="workflow_dispatch", actor_id=OWNER_ID))
    authorize_claims(claims(event_name="issue_comment", actor_id=OWNER_ID))


def test_owner_comment_fresh_runs_keep_exact_receipt_and_no_duplicate_effects(
    tmp_path, monkeypatch
):
    world = ActionsWorld()
    world.run_event = "issue_comment"
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
            for index in range(4):
                world.current_run = str(321 + index)
                claim = claims(
                    event_name="issue_comment",
                    actor_id=OWNER_ID,
                    run_id=world.current_run,
                    jti=f"owner-comment-{index}",
                )
                authorize_claims(claim)
                receipt = await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claim,
                    job_token="synthetic-job-token",
                )
                assert set(receipt) == {
                    "schema",
                    "result",
                    "considered",
                    "published_parts",
                    "delivered",
                    "reconciled",
                    "private_content_exposed",
                }
                assert receipt["delivered"] == int(index == 0)
            assert world.posts == world.provider_calls == 1
            assert (await notes(factory))[0].delivery_state == "delivered"

    asyncio.run(run())


def test_binding_forbids_client_url_workspace_and_incomplete_pair():
    world = ActionsWorld()
    for bad in (
        {**world.binding, "url": "https://foreign"},
        {**world.binding, "workspace_key": "foreign"},
        {**world.binding, "revision": ""},
    ):
        with pytest.raises(WatchVoiceError):
            validate_binding(bad)


def test_private_preflight_before_any_db_provider_or_publication(tmp_path, monkeypatch):
    world = ActionsWorld()
    world.public_repo = True
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
            with pytest.raises(WatchVoiceError, match="private_target"):
                await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claims(),
                    job_token="synthetic-job-token",
                )
            assert await notes(factory) == []
            async with factory() as session:
                assert (await session.scalars(select(WatchVoiceDrainRun))).all() == []
        assert world.posts == world.provider_calls == 0

    asyncio.run(run())


def test_one_run_one_part_full_transcript_next_fresh_run_and_no_retranscription(
    tmp_path, monkeypatch
):
    world = ActionsWorld()
    world.transcript = "Синтетическая 🎙️ запись\n" * 2000
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
            first = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(),
                job_token="synthetic-job-token",
            )
            assert first["published_parts"] == 1 and first["delivered"] == 0
            replay = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(jti="new-jwt-same-run"),
                job_token="synthetic-job-token",
            )
            assert replay["result"] == "replayed"
            assert world.posts == world.provider_calls == 1
            rows = await notes(factory)
            assert len(rows) == 1 and rows[0].data == world.audio and rows[0].next_part == 1
            for index in range(10):
                world.current_run = str(322 + index)
                receipt = await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claims(run_id=world.current_run, jti=f"jwt-{index}"),
                    job_token="synthetic-job-token",
                )
                assert receipt["published_parts"] <= 1
            assert (await notes(factory))[0].delivery_state == "delivered"
            joined = (
                world.issues[0]["body"].split("Расшифровка, часть 1/", 1)[1].split("\n\n", 1)[1]
            )
            joined += "".join(x["body"].split("\n\n", 2)[2] for x in world.comments)
            assert joined == world.transcript
            assert world.provider_calls == 1
            assert all(url != "https://api.github.com/user" for _, url in world.calls)

    asyncio.run(run())


def test_unknown_publication_does_not_block_new_notes_or_reset_claims(tmp_path, monkeypatch):
    world = ActionsWorld()
    world.patches(monkeypatch)

    def handle(request):
        response = world.handle(request)
        if request.method == "POST" and request.url.path == "/repos/markmalina/mark/issues":
            obj = world.issues[-1]
            obj["number"] = 100 + len(world.issues)
            obj["html_url"] = f"https://github.com/markmalina/mark/issues/{obj['number']}"
            return httpx.Response(201, json=obj)
        return response

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http,
        ):
            now = datetime.now(UTC)
            cutover = now - timedelta(hours=1)
            accepted = queued_note(
                world,
                "id:accepted",
                now - timedelta(hours=2),
                transcription_state="transcribed",
                transcript="Synthetic accepted source",
                delivery_state="delivered",
                issue_number=99,
                issue_url="https://github.com/markmalina/mark/issues/99",
                next_part=1,
            )
            unknown = queued_note(
                world,
                "id:unresolved-a",
                now - timedelta(minutes=10),
                transcription_state="transcribed",
                transcript="Synthetic unresolved A",
                delivery_state="unknown",
                attempted_at=now - timedelta(minutes=5),
                last_error="github_post_unknown",
            )
            received = queued_note(world, "id:actionable-b", now - timedelta(minutes=9))
            pending = queued_note(
                world,
                "id:actionable-c",
                now - timedelta(minutes=8),
                transcription_state="transcribed",
                transcript="Synthetic pending C",
            )
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                session.add_all(
                    [
                        accepted,
                        unknown,
                        received,
                        pending,
                        WatchVoiceCursor(
                            workspace_key="mark-personal",
                            account_id="synthetic-owner",
                            cursor="synthetic-cursor",
                            future_cutover_at=cutover,
                        ),
                    ]
                )
                await session.commit()
            world.binding = {
                "schema": "watch-voice-binding.v1",
                "enabled": True,
                "workspace_key": "mark-personal",
                "scope": "future-only",
                "accepted_initial_event_id": accepted.id,
            }
            async with factory() as session:
                before = await session.get(WatchVoiceNote, unknown.id)
                original = {
                    column.key: getattr(before, column.key)
                    for column in WatchVoiceNote.__table__.columns
                }
            for i in range(3):
                world.current_run = str(321 + i)
                result = await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claims(run_id=world.current_run, jti=f"queue-{i}"),
                    job_token="synthetic-job-token",
                )
                assert result["considered"] == 1 and result["published_parts"] <= 1
                assert result["published_parts"] == (1 if i < 2 else 0)
            assert world.provider_calls == 1 and world.posts == 2
            assert len({issue["number"] for issue in world.issues}) == 2
            rows = {note.id: note for note in await notes(factory)}
            assert (
                rows[received.id].delivery_state == rows[pending.id].delivery_state == "delivered"
            )
            assert {
                column.key: getattr(rows[unknown.id], column.key)
                for column in WatchVoiceNote.__table__.columns
            } == original
            replay = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(run_id=world.current_run, jti="new-jti-same-consumed-run"),
                job_token="synthetic-job-token",
            )
            assert replay["result"] == "replayed" and world.provider_calls == 1 and world.posts == 2
            async with factory() as session:
                assert len((await session.scalars(select(WatchVoiceDrainRun))).all()) == 3
                cursor = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert cursor.future_cutover_at.replace(tzinfo=UTC) == cutover

    asyncio.run(run())


def test_stale_workflow_or_main_fail_before_run_consumption(tmp_path):
    world = ActionsWorld()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
            client = ActionsInboxClient(http, "synthetic-job-token")
            world.current_sha = "b" * 40
            with pytest.raises(WatchVoiceError, match="stale_main"):
                await client.preflight_actions(claims(), PIN)
            world.current_sha = SHA
            world.workflow = b"unreviewed"
            with pytest.raises(WatchVoiceError, match="workflow_mismatch"):
                await client.preflight_actions(claims(), PIN)

    asyncio.run(run())


def test_future_mode_requires_delivered_initial_proof(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            binding = validate_binding(
                {
                    "schema": "watch-voice-binding.v1",
                    "enabled": True,
                    "workspace_key": "mark-personal",
                    "scope": "future-only",
                    "accepted_initial_event_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                }
            )
            with pytest.raises(WatchVoiceError, match="initial_acceptance"):
                await bound_settings(
                    action_settings(), binding, factory, job_token="synthetic-job-token"
                )

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["note", "transcript", "delivery", "issue", "cursor"])
def test_future_admission_cannot_install_watermark_without_initial_acceptance(
    tmp_path, monkeypatch, missing
):
    world = ActionsWorld()
    world.patches(monkeypatch)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http,
        ):
            initial = queued_note(
                world, "id:initial-acceptance", datetime.now(UTC),
                transcription_state="received" if missing == "transcript" else "transcribed",
                transcript="Synthetic initial receipt",
                delivery_state="pending" if missing == "delivery" else "delivered",
                issue_url=None if missing == "issue" else "https://github.com/markmalina/mark/issues/1",
            )
            world.binding = {
                "schema": "watch-voice-binding.v1", "enabled": True,
                "workspace_key": "mark-personal", "scope": "future-only",
                "accepted_initial_event_id": initial.id,
            }
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                if missing != "note":
                    session.add(initial)
                session.add(WatchVoiceCursor(
                    workspace_key="mark-personal", account_id="synthetic-owner",
                    cursor=None if missing == "cursor" else "kept-initial-cursor",
                ))
                await session.commit()
            with pytest.raises(WatchVoiceError, match="initial_acceptance_required"):
                await drain(
                    settings=action_settings(), factory=factory, http=http,
                    claims=claims(), job_token="synthetic-job-token",
                )
            assert world.provider_calls == world.posts == 0
            assert not any("dropbox" in url for _, url in world.calls)
            async with factory() as session:
                state = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert state.future_cutover_at is None
                assert state.cursor == (None if missing == "cursor" else "kept-initial-cursor")
                assert not (await session.scalars(select(WatchVoiceDrainRun))).all()

    asyncio.run(run())


def test_same_run_or_jti_replay_is_durable(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            assert await claim_run(factory, claims())
            assert not await claim_run(factory, claims(jti="different"))
            assert not await claim_run(factory, claims(run_id="999"))

    asyncio.run(run())


def test_real_signature_issuer_audience_expiry_and_algorithm(monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt
    from app.core import watch_voice_actions_oidc as oidc
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    key = generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key = generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        oidc,
        "_jwks_client",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    now = int(time.time())
    base = claims(
        iss="https://token.actions.githubusercontent.com",
        aud=oidc.AUDIENCE,
        nbf=now - 1,
        iat=now,
        exp=now + 300,
    )

    async def check():
        good = jwt.encode(base, key, algorithm="RS256")
        assert (
            await oidc.authenticate(authorization="Bearer " + good, settings=action_settings())
        )["run_id"] == "321"
        for payload, signer, algorithm in (
            ({**base, "iss": "https://foreign.invalid"}, key, "RS256"),
            ({**base, "aud": "wrong-audience"}, key, "RS256"),
            ({**base, "exp": now - 100}, key, "RS256"),
            (base, wrong_key, "RS256"),
            (base, "synthetic-hmac-key-32-characters-long", "HS256"),
        ):
            bad = jwt.encode(payload, signer, algorithm=algorithm)
            with pytest.raises(GitHubOidcError, match="invalid"):
                await oidc.authenticate(authorization="Bearer " + bad, settings=action_settings())
        with pytest.raises(GitHubOidcError, match="disabled"):
            await oidc.authenticate(
                authorization="Bearer " + good,
                settings=action_settings().model_copy(
                    update={
                        "gate_watch_voice_actions_enabled": False,
                    }
                ),
            )

    asyncio.run(check())


def test_unknown_post_reconciles_without_second_post_or_provider(tmp_path, monkeypatch):
    world = ActionsWorld()
    world.fail_post = "lost_ack"
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
            receipt = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(),
                job_token="synthetic-job-token",
            )
            assert receipt["published_parts"] == 0
            assert (await notes(factory))[0].delivery_state == "unknown"
            world.current_run = "322"
            receipt = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(run_id="322", jti="new-jwt"),
                job_token="synthetic-job-token",
            )
            assert receipt["reconciled"] == 1 and receipt["delivered"] == 1
            assert world.posts == world.provider_calls == 1

    asyncio.run(run())


def test_parallel_same_run_consumed_once(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            effects = await asyncio.gather(*(claim_run(factory, claims()) for _ in range(4)))
            assert effects.count(True) == 1

    asyncio.run(run())


def test_foreign_status_does_not_read_journal():
    from unittest.mock import AsyncMock

    from app.infrastructure.watch_voice_actions import operational_status

    session = AsyncMock()
    payload = asyncio.run(operational_status(session, action_settings(), scope_allowed=False))
    assert payload["run_claim_count"] == 0 and payload["delivery_counts"] == {}
    assert payload["server_writer_configuration"] is None
    session.execute.assert_not_called()
    session.scalar.assert_not_called()


@pytest.mark.parametrize("configured", [False, True])
def test_owner_writer_preflight_is_presence_only_without_io(configured):
    from unittest.mock import AsyncMock

    from app.api.mcp_schemas import GATE_STATUS_OUTPUT_SCHEMA
    from app.infrastructure.watch_voice_actions import operational_status
    from jsonschema import validate
    from pydantic import SecretStr

    token = "synthetic-private-writer-token"
    writer = "synthetic-private-writer-identity"
    config = action_settings().model_copy(
        update={
            "gate_watch_voice_actions_enabled": False,
            "gate_mark_github_token": SecretStr(token if configured else ""),
            "gate_mark_github_writer": writer if configured else "",
        }
    )
    session = AsyncMock()
    payload = asyncio.run(operational_status(session, config, scope_allowed=True))
    assert payload["server_writer_configuration"] == {
        "token_present": configured,
        "writer_present": configured,
        "verified": False,
    }
    assert token not in json.dumps(payload) and writer not in json.dumps(payload)
    denied = asyncio.run(operational_status(session, config, scope_allowed=False))
    assert denied["server_writer_configuration"] is None
    assert token not in json.dumps(denied) and writer not in json.dumps(denied)
    schema = GATE_STATUS_OUTPUT_SCHEMA["properties"]["watch_voice_actions"]
    validate(payload, schema)
    validate(denied, schema)
    session.execute.assert_not_called()
    session.scalar.assert_not_called()


def test_endpoint_rejects_default_off_and_arbitrary_body(monkeypatch):
    from app.api import watch_voice_routes as route
    from app.core.config import get_settings
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_settings] = lambda: action_settings().model_copy(
        update={
            "gate_watch_voice_actions_enabled": False,
        }
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid"
        ) as client:
            response = await client.post(
                "/internal/watch-voice/drain",
                json={},
                headers={
                    "Authorization": "Bearer synthetic-oidc",
                    "X-Gate-Mark-Job-Token": "synthetic-job",
                },
            )
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
            assert "synthetic-job" not in response.text and "synthetic-oidc" not in response.text
            response = await client.post(
                "/internal/watch-voice/drain", json={"url": "https://foreign"}
            )
            assert response.status_code == 422

    asyncio.run(run())


def test_future_cutover_excludes_history_and_preserves_new_cursor(tmp_path, monkeypatch):
    import uuid
    from datetime import timedelta

    from app.infrastructure import watch_voice_actions as actions
    from app.models.watch_voice import WatchVoiceCursor, WatchVoiceNote

    cutover = datetime.now(UTC) - timedelta(minutes=1)
    clock = [cutover]

    class CutoverClock:
        @staticmethod
        def now(tz=None):
            return clock[0]

    monkeypatch.setattr(actions, "datetime", CutoverClock)

    world = ActionsWorld()
    history = {**world.entry, "id": "id:history", "rev": "bbb123"}
    equal_history = {
        **world.entry,
        "id": "id:equalcutover",
        "rev": "ddd123",
        "server_modified": cutover.isoformat().replace("+00:00", "Z"),
    }
    normal_handle = world.handle
    list_paths = []

    def with_history(request):
        if "/files/list_folder" in request.url.path:
            list_paths.append(request.url.path)
            response = normal_handle(request)
            payload = response.json()
            payload["entries"].append(history)
            payload["entries"].append(equal_history)
            return httpx.Response(200, json=payload)
        return normal_handle(request)

    world.patches(monkeypatch)

    async def run():
        async with (
            database(tmp_path) as factory,
            httpx.AsyncClient(transport=httpx.MockTransport(with_history)) as http,
        ):
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(),
                job_token="synthetic-job-token",
            )
            initial = (await notes(factory))[0]
            assert initial.delivery_state == "delivered"
            assert len(await notes(factory)) == 1  # nonselected source never archived
            old_values = {
                column.key: getattr(initial, column.key)
                for column in WatchVoiceNote.__table__.columns
            }
            old_values.update(
                id=str(uuid.uuid4()),
                file_id="id:oldarchive",
                revision="aaa123",
                # Even newly stored backlog cannot bypass the Dropbox watermark.
                received_at=cutover + timedelta(seconds=1),
                dropbox_modified_at=cutover - timedelta(seconds=1),
                transcription_state="received",
                transcript=None,
                delivery_state="pending",
                next_part=0,
                issue_url=None,
                issue_number=None,
                delivered_at=None,
            )
            async with factory() as session:
                session.add(WatchVoiceNote(**old_values))
                await session.commit()
            world.binding = {
                "schema": "watch-voice-binding.v1",
                "enabled": True,
                "workspace_key": "mark-personal",
                "scope": "future-only",
                "accepted_initial_event_id": initial.id,
            }
            world.current_run = "322"
            before_downloads = sum(url.endswith("/files/download") for _, url in world.calls)
            skipped = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(run_id="322", jti="jwt-old-delta"),
                job_token="synthetic-job-token",
            )
            assert skipped["considered"] == skipped["published_parts"] == 0
            assert world.provider_calls == world.posts == 1
            assert (
                sum(url.endswith("/files/download") for _, url in world.calls) == before_downloads
            )
            assert len(await notes(factory)) == 2
            async with factory() as session:
                cursor = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                first_cutover = cursor.future_cutover_at
                assert first_cutover.replace(tzinfo=UTC) == cutover
                assert cursor.cursor == "synthetic-cursor"
            clock[0] = cutover + timedelta(days=1)
            world.entry = {
                **world.entry,
                "id": "id:newfuture",
                "rev": "ccc123",
                "server_modified": (cutover + timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            }
            world.current_run = "323"
            receipt = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(run_id="323", jti="jwt-future"),
                job_token="synthetic-job-token",
            )
            assert receipt["published_parts"] == receipt["delivered"] == 1
            assert world.provider_calls == world.posts == 2
            rows = await notes(factory)
            old = next(row for row in rows if row.file_id == "id:oldarchive")
            assert old.transcription_state == "received" and old.transcript is None
            assert not any(row.file_id in {"id:history", "id:equalcutover"} for row in rows)
            assert len(rows) == 3
            assert (
                sum(url.endswith("/files/download") for _, url in world.calls)
                == before_downloads + 1
            )
            async with factory() as session:
                cursor = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert cursor.future_cutover_at == first_cutover
            assert list_paths == [
                "/2/files/list_folder",
                "/2/files/list_folder/continue",
                "/2/files/list_folder/continue",
            ]

    asyncio.run(run())
