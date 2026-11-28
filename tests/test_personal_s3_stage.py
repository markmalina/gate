"""Staging composition with signed Work OAuth and a provider-free RU worker."""

from __future__ import annotations

import hashlib
import tomllib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.api import personal_stage
from app.api.personal_command_mcp import PERSONAL_COMMAND_SCOPES
from app.api.personal_stage_file import FIXTURE_PATH
from app.application.personal_telegram_transport import ProviderOutcome, ProviderSendReceipt
from app.infrastructure.database import DatabaseRuntime
from app.personal_telegram import s3_stage
from app.personal_telegram.cloud_session import StoredCloudSession
from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.command_store import PersonalRuntimeBase, RuntimeBindingRow
from app.personal_telegram.runtime_cli import RuntimeSettings
from app.personal_telegram.s2_session_vault import SESSION_REF
from app.personal_telegram.s3_stage import S3_MIGRATION, S3StageJournal, S3StageSettings
from app.personal_telegram.saved_messages_dispatch import SavedMessagesApproval
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_mcp_oauth
from test_personal_command_mcp import ISSUER, RESOURCE, _token
from test_personal_command_runtime import RecordingSavedMessagesFence
from test_personal_work_stage import call, rpc

stage_signing_key = test_mcp_oauth.signing_key


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config():
    return S3StageSettings(
        GATE_ENV="staging",
        enabled=True,
        database_url="postgresql+asyncpg://gate_s2@gate-ru-store.railway.internal/gate_s2_ru",
        master_key=Fernet.generate_key().decode(),
        resource=RESOURCE,
        issuer=ISSUER,
        jwks_url=ISSUER + "jwks",
        client_id="work-client",
        workspace="mark-personal",
        owner="mark",
        binding_id=uuid4(),
        grant_id=uuid4(),
        grant_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_ref="s3-synthetic-approval",
        expected_provider_owner=42,
    )


@pytest.fixture
async def staging_db(config, monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's3.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = RuntimeSettings(
        enabled=True,
        database_url=config.database_url,
        workspace=config.workspace,
        owner=config.owner,
        binding_id=config.binding_id,
        session_key=Fernet.generate_key().decode(),
        command_key=Fernet.generate_key().decode(),
        expected_provider_owner=42,
        GATE_PERSONAL_TELEGRAM_API_ID=123,
        GATE_PERSONAL_TELEGRAM_API_HASH="a" * 32,
    )
    async with engine.begin() as conn:
        await conn.run_sync(PersonalRuntimeBase.metadata.create_all)
        await conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        await conn.execute(
            text("INSERT INTO alembic_version VALUES (:head)"), {"head": S3_MIGRATION}
        )
        await conn.execute(
            text(
                "CREATE TABLE accounts (id TEXT, owner TEXT, telegram_user_id INTEGER, "
                "is_active BOOLEAN, send_enabled BOOLEAN)"
            )
        )
        await conn.execute(
            text("INSERT INTO accounts VALUES (:id, 'mark', 42, true, true)"),
            {"id": config.scope().account_id.hex},
        )
    async with sessions.begin() as session:
        scope = config.scope()
        session.add(
            RuntimeBindingRow(
                id=scope.binding_id,
                workspace=scope.workspace,
                account_id=str(scope.account_id),
                owner_subject=scope.owner_subject,
                session_generation=1,
                ciphertext=runtime.cipher().seal(scope, session="", generation=1),
                revision=0,
                key_version=runtime.key_version,
                custody_state="prepared",
            )
        )

    @asynccontextmanager
    async def local_database(dsn):
        assert dsn == config.database_url.get_secret_value()
        yield DatabaseRuntime(engine, sessions)

    monkeypatch.setattr(s3_stage, "database_runtime_lifespan", local_database)
    monkeypatch.setattr(personal_stage, "database_runtime_lifespan", local_database)
    monkeypatch.setattr(s3_stage, "RuntimeSettings", lambda **_: runtime)
    try:
        yield sessions
    finally:
        await engine.dispose()


def test_default_off_and_explicit_stage_manifest(monkeypatch):
    monkeypatch.delenv("GATE_PERSONAL_S3_STAGE_ENABLED", raising=False)
    with pytest.raises(CommandRuntimeError, match="s3_stage_disabled"):
        personal_stage.create_s3_app()
    manifest = tomllib.loads(Path("railway.personal-s3.toml").read_text())
    start = manifest["deploy"]["startCommand"]
    assert "GATE_PERSONAL_S3_STAGE_ENABLED" in start
    assert "app.api.personal_stage:create_s3_app --factory" in start
    assert "run-one" not in start and "alembic" not in start
    # run-one lives in the volume-bearing S2 image and imports the Work journal.
    assert "httpx" in Path("Dockerfile.personal-s2").read_text()


@pytest.mark.parametrize(
    "override",
    [
        {"GATE_ENV": "production"},
        {"database_url": "postgresql+asyncpg://gate_s2@localhost/gate_s2_ru"},
        {"resource": "https://gate-mtproto-s1-staging.up.railway.app/mcp"},
        {"file_host": "fixture.oaiusercontent.com"},
    ],
)
def test_wrong_environment_database_or_existing_route_is_rejected(config, override):
    values = config.model_dump()
    values["GATE_ENV"] = values.pop("environment")
    with pytest.raises(ValidationError):
        S3StageSettings.model_validate({**values, **override})


@pytest.mark.anyio
async def test_validation_and_missing_worker_approval_do_not_open_database(config, monkeypatch):
    def forbidden(_dsn):
        pytest.fail("Database was opened")

    monkeypatch.setattr(s3_stage, "database_runtime_lifespan", forbidden)
    assert await s3_stage.run_stage("validate", config, {}) == {
        "account": "RU",
        "state": "configuration_valid",
        "provider_calls": 0,
    }
    with pytest.raises(CommandRuntimeError, match="s3_exact_operation_required"):
        await s3_stage.run_stage("run-one", config, {})
    approval = SavedMessagesApproval(uuid4(), config.grant_id, "b" * 64, config.grant_expires_at)
    with pytest.raises(CommandRuntimeError, match="s3_worker_disabled"):
        await s3_stage.run_stage("run-one", config, {}, approval=approval)


@pytest.mark.anyio
async def test_prepared_account_and_exact_migration_are_required(config, staging_db):
    journal = S3StageJournal(staging_db, config)
    await journal.check_binding()
    assert await s3_stage.run_stage("preflight", config, {}) == {
        "account": "RU",
        "state": "journal_ready",
        "provider_calls": 0,
    }
    async with staging_db.begin() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='0125'"))
    with pytest.raises(CommandRuntimeError, match="s3_stage_schema_mismatch"):
        await journal.check_binding()
    async with staging_db.begin() as session:
        await session.execute(
            text("UPDATE alembic_version SET version_num=:head"), {"head": S3_MIGRATION}
        )
        await session.execute(text("UPDATE accounts SET telegram_user_id=77"))
    with pytest.raises(CommandRuntimeError, match="s3_account_pin_mismatch"):
        await journal.check_binding()


@pytest.mark.anyio
async def test_one_queued_work_operation_reaches_the_shared_worker_and_receipt(
    config, staging_db, stage_signing_key, monkeypatch
):
    token = _token(stage_signing_key)
    fake_fence = RecordingSavedMessagesFence()
    vault_reads = []
    sent = []

    def vault(_environment):
        vault_reads.append(True)
        return StoredCloudSession(123, 42, SESSION_REF, "synthetic")

    class Client:
        connected = False

        def __init__(self, _session, _application):
            pass

        async def connect(self):
            self.connected = True

        async def is_user_authorized(self):
            return True

        async def owner_id(self):
            return 42

        def is_connected(self):
            return self.connected

        async def disconnect(self):
            self.connected = False

        async def send_saved_message(self, message, random_id):
            sent.append((message, random_id))
            return ProviderSendReceipt(ProviderOutcome.SENT, "telegram_321", True)

    monkeypatch.setattr(s3_stage, "open_runtime_s2_session", vault)
    monkeypatch.setattr(s3_stage, "TelethonRuntimeClient", Client)
    monkeypatch.setattr(s3_stage, "SqlPersonalTelegramSendFence", lambda *a, **kw: fake_fence)
    app = personal_stage.create_app(config)
    environment = {"GATE_PERSONAL_RUNTIME_ENABLED": "true"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
        ) as client,
    ):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get(FIXTURE_PATH)).status_code == 404
        assert (await rpc(client, "initialize"))["result"]["serverInfo"][
            "name"
        ] == "gate-personal-s3"
        tools = (await rpc(client, "tools/list"))["result"]["tools"]
        assert [tool["name"] for tool in tools] == list(PERSONAL_COMMAND_SCOPES)
        no_scope = _token(stage_signing_key, omit=("gate_workspace_id", "gate_account_ids"))
        assert (await call(client, "readiness", {}, no_scope))["isError"]

        before = (await call(client, "readiness", {}, token))["structuredContent"]
        assert "test_target" not in before and not before["runtime_connected"]
        assert (await s3_stage.run_stage("provision", config, {}))["provider_calls"] == 0
        after = (await call(client, "readiness", {}, token))["structuredContent"]
        message = "synthetic S3"
        prepared = (
            await call(
                client,
                "prepare",
                {**after["test_target"], "request_key": "s3-one", "text": message},
                token,
            )
        )["structuredContent"]
        operation = {"operation_ref": prepared["operation_ref"]}
        queued = (await call(client, "execute", operation, token))["structuredContent"]
        assert queued["state"] == "queued" and queued["sent"] is False
        assert sent == [] and vault_reads == []

        approval = SavedMessagesApproval(
            UUID(hex=operation["operation_ref"].removeprefix("operation_")),
            config.grant_id,
            hashlib.sha256(message.encode()).hexdigest(),
            datetime.now(UTC) + timedelta(minutes=1),
        )
        with pytest.raises(CommandRuntimeError, match="s3_approved_text_mismatch"):
            await s3_stage.run_stage(
                "run-one", config, environment, approval=replace(approval, text_sha256="b" * 64)
            )
        assert vault_reads == []

        worker = await s3_stage.run_stage("run-one", config, environment, approval=approval)
        assert worker == {"account": "RU", "state": "disconnected", "error_code": None}
        assert len(sent) == 1 and sent[0][0] == message
        assert fake_fence.reservations[0].physical_peer_key == "user:42"
        result = (await call(client, "result", operation, token))["structuredContent"]
        assert result["state"] == "succeeded" and result["sent"] is True
        assert result["receipt_ref"] == "telegram_321"
        with pytest.raises(CommandRuntimeError, match="s3_approved_operation_not_queued"):
            await s3_stage.run_stage("run-one", config, environment, approval=approval)
        assert len(sent) == len(vault_reads) == 1
