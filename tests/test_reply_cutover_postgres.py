"""Actual baseline-to-guard cutover proof in a newly created synthetic database."""

import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from app.api.health_routes import reply_cutover_health
from app.core.config import Settings
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.reply_cutover import BASELINE_REVISION, GUARDED_REVISION
from app.models import canonical, relationship_crm  # noqa: F401
from app.models.domain import SendAttempt
from fastapi import FastAPI, Request
from sqlalchemy import make_url, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_reply_sends_postgres as reply_fixtures


def test_actual_cutover_aborts_active_attempts_and_preserves_unknown_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = Settings(_env_file=None).database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for the PostgreSQL cutover proof")

    database_name = "reply_cutover_test_" + uuid4().hex
    isolated_url = make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False,
    )
    environment = {
        **os.environ, "DATABASE_URL": isolated_url, "GATE_SEND_ENABLED": "false",
    }

    async def database_ddl(statement: str) -> None:
        engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    def process(
        *arguments: str, send_enabled: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", *arguments], env={
                **environment, "GATE_SEND_ENABLED": str(send_enabled).lower(),
            },
            capture_output=True, text=True, timeout=60, check=False,
        )

    async def legacy_seed() -> None:
        engine = create_async_engine(isolated_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def legacy_context(*args: object, **kwargs: object) -> str:
            return "synthetic-legacy-context-before-dialog-v1"

        try:
            # The reusable reply fixture normally creates a 0116 snapshot.
            # Here it represents legacy drafts while the real DB is still 0115.
            with monkeypatch.context() as seed_patch:
                seed_patch.setattr(reply_fixtures, "dialog_snapshot", legacy_context)
                seed = await reply_fixtures._seed_reply(sessions, draft_count=3)
            async with sessions.begin() as session:
                session.add_all([
                    SendAttempt(
                        id=uuid4(), reply_draft_id=draft_id,
                        reply_task_id=seed.task_id, conversation_id=seed.conversation_id,
                        account_id=seed.account_id, project_id=seed.project_id,
                        principal_subject="synthetic-cutover-owner", status=status,
                        account_label="RU", target_chat_id=seed.recipient_chat_id,
                        business_connection_id=seed.business_connection_id,
                        error_code="synthetic-provider-unknown" if status == "unknown" else None,
                    )
                    for draft_id, status in zip(
                        seed.draft_ids, ("reserved", "sending", "unknown"), strict=True,
                    )
                ])
        finally:
            await engine.dispose()

    async def stored_state() -> tuple[str, bool, list[tuple[object, ...]]]:
        engine = create_async_engine(isolated_url)
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                table_present = await connection.scalar(text(
                    "SELECT to_regclass('public.reply_dialog_versions') IS NOT NULL"
                ))
                rows = await connection.execute(select(
                    SendAttempt.id, SendAttempt.status, SendAttempt.error_code,
                    SendAttempt.provider_message_id,
                ).order_by(SendAttempt.id))
                return str(revision), bool(table_present), [tuple(row) for row in rows]
        finally:
            await engine.dispose()

    async def settle_synthetic_active_attempts() -> None:
        engine = create_async_engine(isolated_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(update(SendAttempt).where(
                    SendAttempt.status.in_(("reserved", "sending"))
                ).values(status="failed", error_code="synthetic-drain"))
        finally:
            await engine.dispose()

    async def real_health() -> dict[str, object]:
        engine = create_async_engine(isolated_url)
        try:
            app = FastAPI()
            app.state.database_runtime = DatabaseRuntime(engine, async_sessionmaker(engine))
            response = await reply_cutover_health(
                Request({"type": "http", "app": app, "headers": []}),
                authorization="Bearer synthetic-cutover-admin",
                settings=Settings(
                    _env_file=None, ADMIN_BEARER_TOKEN="synthetic-cutover-admin",
                    RAILWAY_GIT_COMMIT_SHA="a" * 40, GATE_SEND_ENABLED=False,
                ),
            )
            assert response.status_code == 200
            return json.loads(response.body)
        finally:
            await engine.dispose()

    asyncio.run(database_ddl(f'CREATE DATABASE "{database_name}"'))
    try:
        baseline = process("alembic", "upgrade", BASELINE_REVISION)
        assert baseline.returncode == 0, baseline.stderr
        asyncio.run(legacy_seed())
        initial_state = asyncio.run(stored_state())
        assert initial_state[:2] == (BASELINE_REVISION, False)

        blocked = process("app.worker.reply_cutover")
        assert blocked.returncode == 1
        assert json.loads(blocked.stdout) == {
            "schema_revisions": [BASELINE_REVISION], "reserved": 1,
            "sending": 1, "unknown": 1,
        }
        assert asyncio.run(stored_state()) == initial_state

        asyncio.run(settle_synthetic_active_attempts())
        drained_state = asyncio.run(stored_state())
        drained_rows = drained_state[2]
        unknown_row = next(row for row in drained_rows if row[1] == "unknown")
        enabled_baseline = process("app.worker.reply_cutover", send_enabled=True)
        assert enabled_baseline.returncode == 1
        assert asyncio.run(stored_state()) == drained_state
        migrated = process("app.worker.reply_cutover")
        assert migrated.returncode == 0, migrated.stderr
        after = asyncio.run(stored_state())
        assert after[:2] == (GUARDED_REVISION, True)
        assert after[2] == drained_rows
        assert next(row for row in after[2] if row[1] == "unknown") == unknown_row

        replay = process("app.worker.reply_cutover", send_enabled=True)
        assert replay.returncode == 0, replay.stderr
        assert asyncio.run(stored_state()) == after
        assert asyncio.run(real_health()) == {
            "status": "observed", "deployment_sha": "a" * 40,
            "schema_revisions": [GUARDED_REVISION], "reserved": 0,
            "sending": 0, "unknown": 1, "send_enabled": False,
            "guard_contract": "dialog-v1",
        }
    finally:
        asyncio.run(database_ddl(f'DROP DATABASE "{database_name}"'))
