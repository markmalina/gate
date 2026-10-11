"""Real PostgreSQL contention tests; separate disposable schema, no provider."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
)
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    LeaseToken,
    PersonalRuntimeBase,
    RuntimeAttemptRow,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.session_persistence import SessionCipher
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 11, 21, tzinfo=UTC)
TTL = timedelta(seconds=30)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pg_runtime():
    url = os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql+asyncpg://"):
        pytest.skip("DATABASE_URL PostgreSQL is required for runtime contention tests")
    schema = f"personal_command_test_{uuid4().hex}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        scope = RuCommandScope("synthetic-workspace", TELEGRAM_MARK_RU_ACCOUNT_ID, uuid4(), "owner")
        cipher = SessionCipher(key=Fernet.generate_key(), key_version="test-v1")
        async with sessions.begin() as session:
            session.add(
                RuntimeBindingRow(
                    id=scope.binding_id,
                    workspace=scope.workspace,
                    account_id=str(scope.account_id),
                    owner_subject=scope.owner_subject,
                    session_generation=1,
                    ciphertext=cipher.seal(scope, session="", generation=1),
                    revision=0,
                    key_version=cipher.key_version,
                    custody_state="prepared",
                )
            )
        store = SqlCommandStore(
            sessions, scope=scope, payload_cipher=CommandPayloadCipher(Fernet.generate_key())
        )
        principal = Principal(
            "owner",
            "oauth2",
            frozenset({"gate:personal:send_text"}),
            AccessScope(scope.workspace, frozenset({scope.account_id}), frozenset()),
        )
        delegation = CommandDelegation(
            uuid4(),
            scope,
            "work-client",
            frozenset({"send_text"}),
            frozenset({"user:42"}),
            NOW + timedelta(hours=1),
        )
        yield SimpleNamespace(
            scope=scope,
            cipher=cipher,
            store=store,
            sessions=sessions,
            principal=principal,
            delegation=delegation,
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def prepare(runtime, key):
    return await runtime.store.prepare(
        principal=runtime.principal,
        delegation=runtime.delegation,
        capability="send_text",
        peer=PeerIdentity("user", 42),
        request_key=key,
        payload={"text": "synthetic"},
        now=NOW,
    )


@pytest.mark.anyio
async def test_postgres_only_one_concurrent_tcp_owner_can_be_admitted(pg_runtime):
    results = await asyncio.gather(
        *(pg_runtime.store.acquire_owner(now=NOW, ttl=TTL) for _ in range(8)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, LeaseToken) for result in results) == 1
    assert all(
        isinstance(result, LeaseToken)
        or isinstance(result, CommandRuntimeError)
        and str(result) == "session_owner_unavailable"
        for result in results
    )


@pytest.mark.anyio
async def test_postgres_concurrent_prepare_preserves_one_exact_intent(pg_runtime):
    results = await asyncio.gather(*(prepare(pg_runtime, "same-intent") for _ in range(8)))
    assert len({result.operation_id for result in results}) == 1
    assert sum(not result.replay for result in results) == 1


@pytest.mark.anyio
async def test_postgres_stale_session_revision_cannot_overwrite_new_credential(pg_runtime):
    payload = pg_runtime.cipher.seal(pg_runtime.scope, session="", generation=1)
    results = await asyncio.gather(
        *(
            pg_runtime.store.compare_and_swap(
                pg_runtime.scope,
                expected_revision=0,
                ciphertext=payload,
                key_version="test-v1",
                state="transition",
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    assert (
        sum(
            isinstance(result, CommandRuntimeError) and str(result) == "session_revision_conflict"
            for result in results
        )
        == 1
    )
    assert (await pg_runtime.store.load(pg_runtime.scope)).revision == 1


@pytest.mark.anyio
async def test_postgres_concurrent_claim_cannot_double_dispatch_same_peer(pg_runtime):
    for key in ("intent-1", "intent-2"):
        prepared = await prepare(pg_runtime, key)
        await pg_runtime.store.execute(
            prepared.operation_id,
            principal=pg_runtime.principal,
            delegation=pg_runtime.delegation,
            now=NOW,
        )
    token = await pg_runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await pg_runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    claims = await asyncio.gather(*(pg_runtime.store.claim(token, now=NOW) for _ in range(4)))
    assert sum(command is not None for command in claims) == 1
    async with pg_runtime.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RuntimeAttemptRow)) == 1
