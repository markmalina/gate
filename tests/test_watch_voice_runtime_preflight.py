"""Authenticated metadata observation, real isolated schema, no provider effects."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from app.api import health_routes
from app.application.readiness import CheckReadinessService
from app.core.config import Settings, get_settings
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.readiness import SqlAlchemyAlembicReadinessProbe
from app.infrastructure.watch_voice_journal import (
    CURRENT_CYCLE,
    expect_worker,
    finish_cycle,
    pulse,
    start_cycle,
)
from app.infrastructure.watch_voice_release import RUNTIME_PREFLIGHT_SCHEMA, runtime_preflight
from fastapi import FastAPI
from jsonschema import validate
from sqlalchemy import event, text
from tests.test_watch_voice import settings
from tests.test_watch_voice_durable import db

HEAD = "0117_watch_voice_early_inbox"
SECRET = "synthetic-admin-private-secret"


def app(cfg, factory=None, readiness=None):
    instance = FastAPI()
    instance.include_router(health_routes.router)
    instance.dependency_overrides[get_settings] = lambda: cfg
    if factory is not None:
        instance.state.database_runtime = DatabaseRuntime(factory.kw["bind"], factory)
    if readiness is not None:
        instance.state.readiness_service = readiness
    return instance


async def schema_probe(factory, head=HEAD):
    async with factory() as session:
        await session.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(64) PRIMARY KEY)")
        )
        await session.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
        await session.commit()
    return CheckReadinessService(
        SqlAlchemyAlembicReadinessProbe(engine=factory.kw["bind"], expected_heads=frozenset({HEAD}))
    )


@pytest.mark.parametrize("token,status", [(None, 403), ("wrong", 403)])
def test_admin_authorization_precedes_runtime_and_database_access(token, status):
    async def scenario():
        cfg = Settings(ADMIN_BEARER_TOKEN=SECRET)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(cfg)), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/ops/watch-voice",
                headers={"Authorization": "Bearer " + token} if token else {},
            )
            assert response.status_code == status
            assert SECRET not in response.text

    asyncio.run(scenario())


def test_missing_admin_credential_does_not_expose_runtime():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(Settings())), base_url="http://test"
        ) as client:
            response = await client.get("/admin/ops/watch-voice")
            assert response.status_code == 503
            assert "runtime" not in response.json()

    asyncio.run(scenario())


def test_actual_schema_settings_and_journal_are_distinct_read_only_facts(tmp_path, monkeypatch):
    cfg = settings().model_copy(update={"admin_bearer_token": SECRET, "deployment_sha": "a" * 40})
    for name, value in (
        ("RAILWAY_DEPLOYMENT_ID", 1),
        ("RAILWAY_PROJECT_ID", 2),
        ("RAILWAY_ENVIRONMENT_ID", 3),
        ("RAILWAY_SERVICE_ID", 4),
    ):
        monkeypatch.setenv(name, str(uuid.UUID(int=value)))
    monkeypatch.setenv("GATE_TEST_UNRELATED_SECRET", "synthetic-secret-not-a-real-dsn")
    statements = []

    async def scenario():
        async with db(tmp_path) as factory:
            service = await schema_probe(factory)
            await expect_worker(factory, cfg)
            instance = app(cfg, factory, service)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=instance), base_url="http://test"
            ) as client:
                first = await client.get(
                    "/admin/ops/watch-voice", headers={"Authorization": "Bearer " + SECRET}
                )
                assert first.status_code == 200
                data = first.json()
                validate(data, RUNTIME_PREFLIGHT_SCHEMA)
                assert data["migrations"] == {
                    "expected_heads": [HEAD],
                    "current_heads": [HEAD],
                    "match": True,
                }
                assert data["configuration"]["driver"] == "persistent"
                assert not data["worker"]["running_driver_proven"]  # startup is not worker proof
                key = await start_cycle(factory, cfg, driver="persistent")
                token = CURRENT_CYCLE.set((factory, key))
                try:
                    await pulse(
                        "dropbox_scanned", owns_cursor=True, scan_finished_at=datetime.now(UTC)
                    )
                    await finish_cycle("success")
                finally:
                    CURRENT_CYCLE.reset(token)

                def record(conn, cursor, statement, params, context, many):
                    statements.append(statement.lstrip().upper())

                event.listen(factory.kw["bind"].sync_engine, "before_cursor_execute", record)
                result = await client.get(
                    "/admin/ops/watch-voice", headers={"Authorization": "Bearer " + SECRET}
                )
                assert result.status_code == 200
                data = result.json()
                validate(data, RUNTIME_PREFLIGHT_SCHEMA)
                assert result.headers["cache-control"] == "no-store"
                assert data["runtime"]["source_sha"] == "a" * 40
                assert data["runtime"]["deployment_id"] == str(uuid.UUID(int=1))
                assert data["worker"]["running_driver_proven"]
                assert data["worker"]["last_cycle_id"] == key
                assert data["effective_start_command"] is None
                assert data["effective_predeploy_command"] is None
                assert not data["predeploy_execution_proven"]
                assert SECRET not in result.text and cfg.dropbox_call_account_id not in result.text
                assert not any(
                    s.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"))
                    for s in statements
                )
                assert not any(
                    "WATCH_VOICE_NOTES" in s or "BILLING_" in s or "WATCH_VOICE_SOURCES" in s
                    for s in statements
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "head,reason",
    [
        ("0115_watch_voice_post_payment", "migration_heads_mismatch"),
        ("synthetic-secret", "migration_state_invalid"),
    ],
)
def test_schema_mismatch_or_invalid_value_cannot_reach_voice_journal(
    tmp_path, monkeypatch, head, reason
):
    async def scenario():
        cfg = settings().model_copy(update={"admin_bearer_token": SECRET})
        async with db(tmp_path) as factory:
            service = await schema_probe(factory, head)
            journal = AsyncMock(
                side_effect=AssertionError("must not read journal before schema match")
            )
            monkeypatch.setattr("app.infrastructure.watch_voice_release.latest_cycle", journal)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app(cfg, factory, service)),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/admin/ops/watch-voice", headers={"Authorization": "Bearer " + SECRET}
                )
                assert response.status_code == 503
                data = response.json()
                validate(data, RUNTIME_PREFLIGHT_SCHEMA)
                assert data["reason"] == reason
                journal.assert_not_awaited()
                assert SECRET not in response.text and "synthetic-secret" not in response.text

    asyncio.run(scenario())


def test_unknown_ids_and_source_are_null_and_probe_error_never_leaks():
    class Broken:
        async def inspect(self):
            raise RuntimeError("synthetic-secret-do-not-copy")

    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "synthetic-secret-do-not-copy"})
        session = AsyncMock()
        result = await runtime_preflight(
            session,
            cfg,
            CheckReadinessService(Broken()),
            {
                "RAILWAY_PROJECT_ID": "synthetic-secret-do-not-copy",
                "OPENAI_API_KEY": "synthetic-secret-do-not-copy",
            },
        )
        validate(result, RUNTIME_PREFLIGHT_SCHEMA)
        assert result["reason"] == "readiness_failed"
        assert all(v is None for v in result["runtime"].values())
        assert "synthetic-secret" not in json.dumps(result)
        assert session.mock_calls == []

    asyncio.run(scenario())


def test_foreign_workspace_is_denied_before_probe_or_journal():
    async def scenario():
        probe = AsyncMock()
        result = await runtime_preflight(
            AsyncMock(),
            settings().model_copy(update={"gate_workspace_id": "foreign"}),
            CheckReadinessService(probe),
            {},
        )
        validate(result, RUNTIME_PREFLIGHT_SCHEMA)
        assert result["reason"] == "workspace_scope_mismatch"
        probe.inspect.assert_not_awaited()

    asyncio.run(scenario())

