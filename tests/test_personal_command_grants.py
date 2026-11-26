"""Durable approved grants: restart, immutable policies and fail-closed boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import TELEGRAM_MARK_BY_ACCOUNT_ID, TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    personal_client_namespace,
)
from app.personal_telegram.command_store import PersonalRuntimeBase, RuntimeBindingRow
from app.personal_telegram.grant_registry import RuntimeGrantRow, SqlGrantRegistry
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 12, 6, tzinfo=UTC)
PEER = PeerIdentity("user", 42)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def make_runtime(engine):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    scope = RuCommandScope("synthetic-workspace", TELEGRAM_MARK_RU_ACCOUNT_ID, uuid4(), "owner")
    principal = Principal(
        "owner",
        "oauth2",
        frozenset({"gate:personal:send_text"}),
        AccessScope(scope.workspace, frozenset({scope.account_id}), frozenset()),
        oauth_issuer="https://auth.gate.example/",
        oauth_client_id="work-client",
    )
    delegation = CommandDelegation(
        uuid4(),
        scope,
        personal_client_namespace(principal),
        frozenset({"send_text"}),
        frozenset({PEER.key}),
        NOW + timedelta(hours=1),
    )
    async with sessions.begin() as session:
        session.add(
            RuntimeBindingRow(
                id=scope.binding_id,
                workspace=scope.workspace,
                account_id=str(scope.account_id),
                owner_subject=scope.owner_subject,
                session_generation=1,
                ciphertext=b"synthetic-session",
                revision=0,
                key_version="synthetic",
                custody_state="prepared",
            )
        )
    key = Fernet.generate_key()
    registry = SqlGrantRegistry(sessions, scope=scope, policy_key=key, key_version="test-v1")
    await registry.install_approved_grant(
        delegation, approval_ref="synthetic-owner-approval", now=NOW
    )
    return SimpleNamespace(
        sessions=sessions,
        scope=scope,
        principal=principal,
        delegation=delegation,
        registry=registry,
        key=key,
    )


@pytest.fixture
async def grants(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'grants.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
    runtime = await make_runtime(engine)
    yield runtime
    await engine.dispose()


async def require(runtime, *, principal=None, now=NOW, capability="send_text", peer=PEER):
    async with runtime.sessions.begin() as session:
        return await runtime.registry.require(
            session,
            principal=principal or runtime.principal,
            delegation_id=runtime.delegation.delegation_id,
            capability=capability,
            peer=peer,
            now=now,
        )


@pytest.mark.anyio
async def test_registry_restart_reconstructs_encrypted_approved_policy(grants):
    grants.registry = SqlGrantRegistry(
        grants.sessions, scope=grants.scope, policy_key=grants.key, key_version="test-v1"
    )
    assert await require(grants) == grants.delegation
    async with grants.sessions() as session:
        row = await session.get(RuntimeGrantRow, grants.delegation.delegation_id)
        assert row is not None
        assert b"send_text" not in row.policy_ciphertext and b"user:42" not in row.policy_ciphertext
        assert row.approval_ref == "synthetic-owner-approval" and row.revision == 0


@pytest.mark.anyio
async def test_grant_install_is_idempotent_but_cannot_expand_or_resurrect(grants):
    assert (
        await grants.registry.install_approved_grant(
            grants.delegation, approval_ref="synthetic-owner-approval", now=NOW
        )
        == grants.delegation
    )
    for changed in [
        replace(grants.delegation, capabilities=frozenset({"send_text", "delete"})),
        replace(grants.delegation, peer_keys=frozenset({"user:43"})),
        replace(grants.delegation, expires_at=NOW + timedelta(days=1)),
        replace(grants.delegation, client_namespace="a" * 64),
    ]:
        with pytest.raises(CommandRuntimeError, match="delegation_policy_conflict"):
            await grants.registry.install_approved_grant(
                changed, approval_ref="synthetic-owner-approval", now=NOW
            )
    await grants.registry.revoke(
        principal=grants.principal,
        delegation_id=grants.delegation.delegation_id,
        expected_revision=0,
        now=NOW,
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await grants.registry.install_approved_grant(
            grants.delegation, approval_ref="synthetic-owner-approval", now=NOW
        )


@pytest.mark.anyio
async def test_revocation_persists_and_stale_revision_cannot_write(grants):
    assert (
        await grants.registry.revoke(
            principal=grants.principal,
            delegation_id=grants.delegation.delegation_id,
            expected_revision=0,
            now=NOW,
        )
        == 1
    )
    with pytest.raises(CommandRuntimeError, match="delegation_revision_conflict"):
        await grants.registry.revoke(
            principal=grants.principal,
            delegation_id=grants.delegation.delegation_id,
            expected_revision=0,
            now=NOW,
        )
    grants.registry = SqlGrantRegistry(
        grants.sessions, scope=grants.scope, policy_key=grants.key, key_version="test-v1"
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await require(grants)


@pytest.mark.anyio
async def test_concurrent_sqlite_revokes_have_one_cas_winner(grants):
    results = await asyncio.gather(
        *(
            grants.registry.revoke(
                principal=grants.principal,
                delegation_id=grants.delegation.delegation_id,
                expected_revision=0,
                now=NOW,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    assert (
        sum(
            isinstance(r, CommandRuntimeError) and str(r) == "delegation_revision_conflict"
            for r in results
        )
        == 1
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await require(grants)


@pytest.mark.anyio
async def test_foreign_client_and_principal_denied_before_decrypt(grants, monkeypatch):
    def no_decrypt(row):
        raise AssertionError("foreign authorization must not decrypt")

    monkeypatch.setattr(grants.registry, "_decode", no_decrypt)
    for principal in [
        replace(grants.principal, subject="foreign"),
        replace(grants.principal, oauth_client_id="foreign-client"),
        replace(grants.principal, oauth_issuer="https://foreign.example/"),
        replace(grants.principal, auth_method="bearer"),
        replace(grants.principal, oauth_client_id=None),
        replace(grants.principal, scopes=frozenset()),
        replace(
            grants.principal,
            access_scope=replace(grants.principal.access_scope, workspace_id="foreign"),
        ),
        replace(
            grants.principal,
            access_scope=replace(
                grants.principal.access_scope, account_ids=frozenset({TELEGRAM_MARK_BY_ACCOUNT_ID})
            ),
        ),
    ]:
        with pytest.raises(CommandRuntimeError):
            await require(grants, principal=principal)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_at", NOW + timedelta(days=1)),
        ("key_version", "wrong-key"),
        ("approval_ref", "changed-approval"),
        ("policy_ciphertext", b"invalid-synthetic-policy"),
    ],
)
async def test_stored_metadata_and_ciphertext_tampering_fails_closed(grants, field, value):
    async with grants.sessions.begin() as session:
        await session.execute(update(RuntimeGrantRow).values(**{field: value}))
    with pytest.raises(CommandRuntimeError, match="^delegation_policy_unavailable$"):
        await require(grants)


@pytest.mark.anyio
async def test_policy_ciphertext_cannot_be_transplanted_between_grants(grants):
    second = replace(grants.delegation, delegation_id=uuid4())
    await grants.registry.install_approved_grant(
        second, approval_ref="synthetic-owner-approval", now=NOW
    )
    async with grants.sessions.begin() as session:
        row = await session.get(RuntimeGrantRow, second.delegation_id)
        await session.execute(
            update(RuntimeGrantRow)
            .where(RuntimeGrantRow.id == grants.delegation.delegation_id)
            .values(policy_ciphertext=row.policy_ciphertext)
        )
    with pytest.raises(CommandRuntimeError, match="delegation_policy_unavailable"):
        await require(grants)


@pytest.mark.anyio
async def test_worker_rechecks_expiry_scope_and_revocation(grants):
    for owner, namespace, capability, peer, now, code in [
        ("other", grants.delegation.client_namespace, "send_text", PEER, NOW, "ru_scope_denied"),
        ("owner", "a" * 64, "send_text", PEER, NOW, "delegation_not_found"),
        ("owner", grants.delegation.client_namespace, "delete", PEER, NOW, "command_scope_denied"),
        (
            "owner",
            grants.delegation.client_namespace,
            "send_text",
            PeerIdentity("channel", 42),
            NOW,
            "command_scope_denied",
        ),
        (
            "owner",
            grants.delegation.client_namespace,
            "send_text",
            PEER,
            grants.delegation.expires_at,
            "delegation_inactive",
        ),
    ]:
        async with grants.sessions.begin() as session:
            with pytest.raises(CommandRuntimeError, match=code):
                await grants.registry.for_operation(
                    session,
                    owner_subject=owner,
                    client_namespace=namespace,
                    delegation_id=grants.delegation.delegation_id,
                    capability=capability,
                    peer=peer,
                    now=now,
                )


@pytest.mark.anyio
async def test_invalid_setup_and_missing_binding_do_not_install(grants):
    for changed in [
        replace(grants.delegation, client_namespace="caller-invented"),
        replace(grants.delegation, expires_at=NOW),
        replace(grants.delegation, expires_at=NOW.replace(tzinfo=None)),
        replace(grants.delegation, peer_keys=frozenset({"user:042"})),
        replace(grants.delegation, capabilities=frozenset()),
        replace(grants.delegation, capabilities=frozenset({"invalid-capability"})),
    ]:
        with pytest.raises(CommandRuntimeError):
            await grants.registry.install_approved_grant(
                changed, approval_ref="synthetic-owner-approval", now=NOW
            )
    foreign = replace(grants.scope, binding_id=uuid4())
    registry = SqlGrantRegistry(
        grants.sessions, scope=foreign, policy_key=grants.key, key_version="test-v1"
    )
    with pytest.raises(CommandRuntimeError, match="ru_binding_not_found"):
        await registry.install_approved_grant(
            replace(grants.delegation, scope=foreign, delegation_id=uuid4()),
            approval_ref="synthetic-owner-approval",
            now=NOW,
        )
    async with grants.sessions() as session:
        assert len((await session.scalars(select(RuntimeGrantRow))).all()) == 1


@pytest.mark.anyio
async def test_database_adapter_errors_are_redacted(grants, monkeypatch):
    from sqlalchemy.exc import OperationalError

    async def broken(*args, **kwargs):
        raise OperationalError(
            "sensitive-statement", {}, Exception("synthetic-sensitive-db-detail")
        )

    monkeypatch.setattr(grants.registry, "_binding", broken)
    with pytest.raises(CommandRuntimeError, match="^delegation_store_unavailable$"):
        await require(grants)
