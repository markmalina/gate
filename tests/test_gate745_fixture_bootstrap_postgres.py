"""Real PG drill ONLY in the existing disposable CI service; never Railway.

Local absence is an explicit skip, not native fixture acceptance. The fixed
test-only DB is newly created, separately from CI's ordinary `gate` database.
"""

import asyncio
import os
from dataclasses import replace
from typing import Any

import pytest
from app.worker import gate745_fixture_bootstrap as helper
from app.worker.gate745_fixture_policy import BASELINE, GUARDED, FixtureDenied, digest
from app.worker.gate745_fixture_recipe import JOURNAL_DIGEST
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.test_gate745_fixture_bootstrap import NOW, admit_manifest, proof, raw_manifest

CI_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate"
TEST_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate745_synthetic"


def test_real_pg_bootstrap_journal_denial_reset_and_0116_readonly() -> None:
    if os.environ.get("GATE_ENV") != "ci":
        pytest.skip("Requires existing isolated CI PostgreSQL service; no hosted DB access")
    if os.environ.get("DATABASE_URL") != CI_URL:
        pytest.skip("Exact checked-in disposable CI DB binding required")
    asyncio.run(_drill())


async def _drill() -> None:
    admin = create_async_engine(CI_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    created = False
    engine = create_async_engine(TEST_URL, poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname='gate745_synthetic'")
            )
            if exists:
                pytest.fail("Refusing to reuse/drop an existing fixture database")
            await connection.execute(text("CREATE DATABASE gate745_synthetic"))
            created = True
        async with engine.begin() as connection:
            raw = await connection.execute(
                text(
                    "SELECT current_database() AS name, inet_server_addr()::text AS address, "
                    "inet_server_port() AS port, system_identifier::text AS system_identifier "
                    "FROM pg_control_system()"
                )
            )
            physical = raw.mappings().one()
            binding = admit_manifest(raw_manifest(), now=NOW)
            identity = {
                key: binding.target[key]
                for key in (
                    "projectId",
                    "environmentId",
                    "postgresServiceId",
                    "volumeInstanceId",
                )
            }
            identity.update(
                {
                    "databaseName": physical["name"],
                    "serverAddress": physical["address"],
                    "serverPort": physical["port"],
                    "systemIdentifier": physical["system_identifier"],
                }
            )
            binding = replace(
                binding, seed={**binding.seed, "databaseIdentityDigest": digest(identity)}
            )
            result = await connection.run_sync(
                lambda c: helper._operate(c, binding, proof("bootstrap"))
            )
            assert result["schemaRevision"] == BASELINE
            assert result["unknownCount"] == 1
            assert result["unknownJournalDigest"] == JOURNAL_DIGEST
            assert result["beforeUnknownCount"] == 0
            # Actual migrations' default historical accounts/projects were
            # removed: no historical account, active identity, token or data.
            assert await connection.scalar(text("SELECT count(*) FROM accounts")) == 1
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM accounts WHERE is_active OR read_enabled "
                        "OR send_enabled OR telegram_user_id IS NOT NULL"
                    )
                )
                == 0
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_BOOTSTRAP_NOT_EMPTY"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof("bootstrap")))
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE send_attempts SET status='sending'"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_NONLITERAL_STATE"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof()))
            with pytest.raises(FixtureDenied, match="E_FIXTURE_NONLITERAL_STATE"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof("reset")))
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE send_attempts SET status='unknown'"))
            # Exact historical guard 0116 schema shape; no provider operation.
            await connection.execute(
                text(
                    "CREATE TABLE reply_dialog_versions "
                    "(business_connection_id varchar(255) NOT NULL, "
                    "chat_id varchar(255) NOT NULL, revision bigint NOT NULL, "
                    "CONSTRAINT ck_reply_dialog_version_nonnegative CHECK(revision>=0), "
                    "PRIMARY KEY(business_connection_id,chat_id))"
                )
            )
            await connection.execute(
                text("UPDATE alembic_version SET version_num='0116_reply_dialog_versions'")
            )
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            observed = await connection.run_sync(lambda c: helper._operate(c, binding, proof()))
            assert observed["schemaRevision"] == GUARDED
            assert (
                observed["beforeUnknownJournalDigest"]
                == observed["unknownJournalDigest"]
                == JOURNAL_DIGEST
            )
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE send_attempts ADD COLUMN unexpected text"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE send_attempts DROP COLUMN unexpected"))
            await connection.execute(text("CREATE SCHEMA contaminated"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_FOREIGN_SCHEMA"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof("reset")))
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA contaminated"))
            await connection.execute(
                text(
                    "ALTER TABLE reply_dialog_versions DROP CONSTRAINT "
                    "ck_reply_dialog_version_nonnegative"
                )
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE reply_dialog_versions ADD CONSTRAINT "
                    "ck_reply_dialog_version_nonnegative CHECK(revision>=0)"
                )
            )
        async with engine.begin() as connection:
            fresh_reset = replace(
                proof("reset"),
                reset_grant_comment_id="1003",
                reset_grant_body_digest="sha256:" + "a" * 64,
                retained_case_receipt_digest="sha256:" + "b" * 64,
                quiescence_receipt_digest="sha256:" + "c" * 64,
            )
            restored: dict[str, Any] = await connection.run_sync(
                lambda c: helper._operate(c, binding, fresh_reset)
            )
            assert restored["schemaRevision"] == BASELINE
            assert restored["beforeUnknownCount"] == restored["unknownCount"] == 1
            assert (
                restored["beforeUnknownJournalDigest"]
                == restored["unknownJournalDigest"]
                == JOURNAL_DIGEST
            )
    finally:
        await engine.dispose()
        if created:
            async with admin.connect() as connection:
                await connection.execute(text("DROP DATABASE gate745_synthetic"))
        await admin.dispose()
