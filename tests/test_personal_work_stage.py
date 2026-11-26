"""S1 acceptance below the real Work boundary, using signed OAuth and migrated PostgreSQL."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api import personal_stage_file
from app.api.personal_command_mcp import PERSONAL_COMMAND_SCOPES, personal_command_tools
from app.api.personal_stage import create_app
from app.api.personal_stage_file import FILE_TOOL, FIXTURE, FIXTURE_PATH
from app.infrastructure.database import database_runtime_lifespan
from app.models.domain import TELEGRAM_MARK_BY_ACCOUNT_ID, Project
from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.command_store import RuntimeAttemptRow, RuntimeOperationRow
from app.personal_telegram.work_stage import StageJournal, StageSettings, provision
from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import test_mcp_oauth
from test_personal_command_mcp import ISSUER, RESOURCE, _token

stage_signing_key = test_mcp_oauth.signing_key


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def stage():
    explicit = os.environ.get("PERSONAL_COMMAND_TEST_DATABASE_URL")
    if not explicit:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL")
    url = make_url(explicit)
    if url.drivername != "postgresql+asyncpg" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("S1 acceptance requires a disposable localhost PostgreSQL")
    name = "gate_s1_test_" + uuid4().hex
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        dsn = url.set(database=name).render_as_string(hide_password=False)

        def migrate(target):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", target],
                env={**os.environ, "DATABASE_URL": dsn},
                check=True,
                capture_output=True,
            )

        migrate("0123_watch_mark_updates")
        async with database_runtime_lifespan(dsn) as db:
            project = Project(
                id=uuid4(), alias="s1-fixture", name="S1 fixture", adapter_type="fixture"
            )
            async with db.session_factory.begin() as session:
                session.add(project)
            migrate("head")
            async with db.session_factory() as session:
                assert (await session.get(Project, project.id)).name == project.name
                assert (
                    await session.scalar(select(func.count()).select_from(RuntimeOperationRow)) == 0
                )
        config = StageSettings(
            GATE_ENV="test",
            database_url=dsn,
            master_key=Fernet.generate_key().decode(),
            resource=RESOURCE,
            issuer=ISSUER,
            jwks_url=ISSUER + "jwks",
            client_id="work-client",
            workspace="mark-personal",
            owner="mark",
            binding_id=uuid4(),
            grant_id=uuid4(),
            grant_expires_at=datetime.now(UTC) + timedelta(hours=1),
            approval_ref="s1-test",
            file_host="files.oaiusercontent.com",
        )
        yield config
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}"'))
        await admin.dispose()


async def rpc(client, method, params=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    reply = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
    )
    assert reply.status_code == 200
    return reply.json()


async def call(client, action, args, token):
    name = FILE_TOOL if action == "file" else "gate_personal_command_" + action
    return (await rpc(client, "tools/call", {"name": name, "arguments": args}, token))["result"]


class LostResponse(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        # The server has already completed and committed when the client loses the response.
        reply = await self.inner.handle_async_request(request)
        assert reply.status_code == 200
        await reply.aclose()
        raise httpx.ReadError("simulated response loss", request=request)

    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)


@pytest.mark.anyio
async def test_work_journal_after_lost_response_and_process_restart(stage, stage_signing_key):
    key = stage_signing_key
    token = _token(key)
    app = create_app(stage)
    with pytest.raises(CommandRuntimeError, match="synthetic_binding_required"):
        async with app.router.lifespan_context(app):
            pytest.fail("unprovisioned installation started")
    await provision(stage)
    await provision(stage)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=RESOURCE.removesuffix("/mcp")
        ) as client,
    ):
        assert (await rpc(client, "initialize"))["result"]["serverInfo"][
            "name"
        ] == "gate-personal-s1"
        info = (
            await client.get("https://gate.example/.well-known/oauth-protected-resource/mcp")
        ).json()
        assert info["scopes_supported"] == sorted(set(PERSONAL_COMMAND_SCOPES.values()))
        catalog = (await rpc(client, "tools/list"))["result"]["tools"]
        assert [t["name"] for t in catalog] == [*PERSONAL_COMMAND_SCOPES, FILE_TOOL]
        assert (
            len(
                personal_command_tools(stage.oauth_settings())[0]["outputSchema"]["oneOf"][0][
                    "required"
                ]
            )
            == 3
        )
        for descriptor in catalog:
            Draft202012Validator.check_schema(descriptor["inputSchema"])
            Draft202012Validator.check_schema(descriptor["outputSchema"])
        response = await call(client, "readiness", {}, token)
        readiness = response["structuredContent"]
        Draft202012Validator(catalog[0]["outputSchema"]).validate(readiness)
        assert readiness["journal_ready"] and not readiness["runtime_connected"]
        assert (
            "test_target"
            not in (
                await call(client, "readiness", {}, _token(key, scope="gate:personal:jobs:read"))
            )["structuredContent"]
        )
        assert (await call(client, "readiness", {}, None))["_meta"]["mcp/www_authenticate"]
        assert "error" in await rpc(
            client, "tools/call", {"name": "gate_send_approved_reply", "arguments": {}}, token
        )
        args = {
            **readiness["test_target"],
            "request_key": "s1-lost-response",
            "text": "Synthetic S1",
        }
        async with httpx.AsyncClient(
            transport=LostResponse(app), base_url=RESOURCE.removesuffix("/mcp")
        ) as lost:
            with pytest.raises(httpx.ReadError):
                await call(lost, "prepare", args, token)

    app = create_app(stage)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=RESOURCE.removesuffix("/mcp")
        ) as client,
    ):
        retry = (await call(client, "prepare", args, token))["structuredContent"]
        assert retry["state"] == "prepared" and retry["replay"] and not retry["sent"]
        op = {"operation_ref": retry["operation_ref"]}
        assert (await call(client, "prepare", {**args, "text": "changed"}, token))["isError"]
        queued = (await call(client, "execute", op, token))["structuredContent"]
        assert queued["state"] == "queued" and not queued["sent"]
        for claims in (
            {"azp": "other-client"},
            {"sub": "other-owner"},
            {"gate_workspace_id": "other"},
            {"gate_account_ids": [str(TELEGRAM_MARK_BY_ACCOUNT_ID)]},
        ):
            for action in ("status", "result", "cancel"):
                assert (await call(client, action, op, _token(key, **claims)))["isError"]
        assert (await call(client, "cancel", op, _token(key, scope="gate:personal:jobs:read")))[
            "isError"
        ]
        assert (await call(client, "status", op, token))["structuredContent"]["state"] == "queued"
        assert (await call(client, "cancel", op, token))["structuredContent"][
            "state"
        ] == "cancelled"
        result = (await call(client, "result", op, token))["structuredContent"]
        assert result["operation_ref"] == op["operation_ref"] and result["state"] == "cancelled"

    async with database_runtime_lifespan(stage.database_url.get_secret_value()) as db:
        journal = StageJournal(db.session_factory, stage)
        async with db.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(RuntimeOperationRow)) == 1
            assert await session.scalar(select(func.count()).select_from(RuntimeAttemptRow)) == 0
        with pytest.raises(CommandRuntimeError, match="session_owner_unavailable"):
            await journal.store.acquire_owner(now=datetime.now(UTC), ttl=timedelta(seconds=5))
        with pytest.raises(CommandRuntimeError, match="session_revision_conflict"):
            await journal.store.compare_and_swap(
                journal.scope,
                expected_revision=0,
                ciphertext=b"x",
                key_version="x",
                state="prepared",
            )


@pytest.mark.anyio
async def test_work_file_contract_and_byte_roundtrip(stage, stage_signing_key, monkeypatch):
    key = stage_signing_key
    token = _token(key)
    await provision(stage)
    app = create_app(stage)
    args = {
        "file": {
            "file_id": "synthetic-file",
            "download_url": "https://files.oaiusercontent.com/fixture",
        }
    }
    received = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield FIXTURE[:4]
            yield FIXTURE[4:]

    def fetch(request):
        received.append(request.url.host)
        return httpx.Response(200, stream=Chunks())

    monkeypatch.setattr(
        personal_stage_file.httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(fetch)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=RESOURCE.removesuffix("/mcp")
        ) as client,
    ):
        assert (await call(client, "file", args, _token(key, azp="other-client")))["isError"]
        bad = {"file": {**args["file"], "download_url": "https://unapproved.example/f"}}
        assert (await call(client, "file", bad, token))["structuredContent"][
            "error"
        ] == "work_file_host_denied"
        assert not received
        result = await call(client, "file", args, token)
        assert not result["isError"]
        meta = result["structuredContent"]
        returned = await client.get(meta["download_url"])
        assert returned.content == FIXTURE
        assert meta["bytes"] == len(FIXTURE)
        assert meta["sha256"] == hashlib.sha256(returned.content).hexdigest()
        assert result["content"][1]["uri"].endswith(FIXTURE_PATH)
        assert received == ["files.oaiusercontent.com"]
        for status, headers, code in [
            (302, {"Location": "https://unapproved.example/f"}, "work_file_unavailable"),
            (200, {"Content-Length": str(len(FIXTURE) + 1)}, "fixture_mismatch"),
        ]:
            monkeypatch.setattr(
                personal_stage_file.httpx,
                "AsyncHTTPTransport",
                lambda status=status, headers=headers, **kwargs: httpx.MockTransport(
                    lambda r: httpx.Response(status, headers=headers, stream=Chunks())
                ),
            )
            assert (await call(client, "file", args, token))["structuredContent"]["error"] == code
