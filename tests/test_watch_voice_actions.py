"""Synthetic private workflow credentials and audio; no network or real content."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime

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
from app.models.watch_voice import WatchVoiceDrainRun
from sqlalchemy import select

from test_watch_voice import World, database, notes, settings

WORKFLOW = b"synthetic-reviewed-workflow"
PIN = hashlib.sha256(WORKFLOW).hexdigest()
SHA = "a" * 40


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

    from app.models.watch_voice import WatchVoiceCursor, WatchVoiceNote

    world = ActionsWorld()
    history = {**world.entry, "id": "id:history", "rev": "bbb123"}
    normal_handle = world.handle
    list_paths = []

    def with_history(request):
        if "/files/list_folder" in request.url.path:
            list_paths.append(request.url.path)
            response = normal_handle(request)
            payload = response.json()
            payload["entries"].append(history)
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
                received_at=datetime.now(UTC) - timedelta(days=1),
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
            world.entry = {**world.entry, "id": "id:newfuture", "rev": "ccc123"}
            history.clear()  # Dropbox continuation must not re-list old history
            world.current_run = "322"
            receipt = await drain(
                settings=action_settings(),
                factory=factory,
                http=http,
                claims=claims(run_id="322", jti="jwt-future"),
                job_token="synthetic-job-token",
            )
            assert receipt["published_parts"] == receipt["delivered"] == 1
            assert world.provider_calls == world.posts == 2
            rows = await notes(factory)
            old = next(row for row in rows if row.file_id == "id:oldarchive")
            assert old.transcription_state == "received" and old.transcript is None
            async with factory() as session:
                cursor = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                assert cursor.future_cutover_at is not None
            assert list_paths == ["/2/files/list_folder", "/2/files/list_folder/continue"]

    asyncio.run(run())
