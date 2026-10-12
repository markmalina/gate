"""Disposable local PostgreSQL grant interleavings; no runtime or Telegram effects."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.command_store import PersonalRuntimeBase
from app.personal_telegram.grant_registry import RuntimeGrantRow
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.test_personal_command_grants import NOW, PEER, make_runtime, require


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pg_grants():
    # Never repurpose a production DATABASE_URL for these destructive schema tests.
    url = os.environ.get("PERSONAL_COMMAND_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit disposable local PERSONAL_COMMAND_TEST_DATABASE_URL required")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or parsed.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise AssertionError("grant contention tests require an explicit local PostgreSQL")
    schema = f"personal_grant_test_{uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
        yield await make_runtime(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.anyio
async def test_postgres_require_transaction_orders_before_revoke(pg_grants):
    started = asyncio.Event()
    async with pg_grants.sessions.begin() as session:
        await pg_grants.registry.require(
            session,
            principal=pg_grants.principal,
            delegation_id=pg_grants.delegation.delegation_id,
            capability="send_text",
            peer=PEER,
            now=NOW,
        )

        async def revoke():
            started.set()
            return await pg_grants.registry.revoke(
                principal=pg_grants.principal,
                delegation_id=pg_grants.delegation.delegation_id,
                expected_revision=0,
                now=NOW,
            )

        task = asyncio.create_task(revoke())
        await started.wait()
        # Read actual PostgreSQL lock wait; timing-only assertions cannot prove ordering.
        async with pg_grants.sessions() as observer:
            for _ in range(100):
                waiting = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND query LIKE '%personal_command_bindings%'"
                    )
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                pytest.fail("revoke did not wait for the require transaction's binding lock")
        assert not task.done()
    assert await asyncio.wait_for(task, 5) == 1
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await require(pg_grants)


@pytest.mark.anyio
async def test_postgres_revoke_transaction_orders_before_require(pg_grants):
    async with pg_grants.sessions.begin() as session:
        row = await pg_grants.registry._row(
            session,
            pg_grants.delegation.delegation_id,
            pg_grants.delegation.client_namespace,
        )
        row.revoked = True
        row.revision += 1
        task = asyncio.create_task(require(pg_grants))
        async with pg_grants.sessions() as observer:
            for _ in range(100):
                waiting = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND query LIKE '%personal_command_bindings%'"
                    )
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                pytest.fail("require did not wait for revocation's binding lock")
        assert not task.done()
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await asyncio.wait_for(task, 5)


@pytest.mark.anyio
async def test_postgres_concurrent_revoke_has_one_revision_winner(pg_grants):
    results = await asyncio.gather(
        *(
            pg_grants.registry.revoke(
                principal=pg_grants.principal,
                delegation_id=pg_grants.delegation.delegation_id,
                expected_revision=0,
                now=NOW,
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    assert (
        sum(
            isinstance(item, CommandRuntimeError) and str(item) == "delegation_revision_conflict"
            for item in results
        )
        == 7
    )
    async with pg_grants.sessions() as session:
        row = await session.scalar(select(RuntimeGrantRow))
        assert row.revoked and row.revision == 1
