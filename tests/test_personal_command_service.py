"""Provider-free public contracts, references and client isolation."""

from __future__ import annotations

import json
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
from app.personal_telegram.command_service import PersonalCommandService
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    PersonalRuntimeBase,
    RuntimeBindingRow,
    RuntimeOperationRow,
    SqlCommandStore,
)
from app.personal_telegram.grant_registry import SqlGrantRegistry
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 12, 6, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def service(tmp_path):
    scope = RuCommandScope("synthetic-workspace", TELEGRAM_MARK_RU_ACCOUNT_ID, uuid4(), "owner")
    principal = Principal(
        "owner",
        "oauth2",
        frozenset(
            {"gate:personal:send_text", "gate:personal:jobs:read", "gate:personal:jobs:cancel"}
        ),
        AccessScope(scope.workspace, frozenset({scope.account_id}), frozenset()),
        "https://auth.gate.example/",
        "work-client",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        session.add(
            RuntimeBindingRow(
                id=scope.binding_id,
                workspace=scope.workspace,
                account_id=str(scope.account_id),
                owner_subject=scope.owner_subject,
                session_generation=1,
                ciphertext=b"not-a-session",
                revision=0,
                key_version="synthetic-v1",
                custody_state="prepared",
            )
        )
    grants = SqlGrantRegistry(
        sessions, scope=scope, policy_key=Fernet.generate_key(), key_version="synthetic-grant-v1"
    )
    delegation = CommandDelegation(
        uuid4(),
        scope,
        personal_client_namespace(principal),
        frozenset({"send_text"}),
        frozenset({"user:42", "channel:42"}),
        NOW + timedelta(hours=1),
    )
    await grants.install_approved_grant(
        delegation, approval_ref="synthetic-owner-approval", now=NOW
    )
    store = SqlCommandStore(
        sessions,
        scope=scope,
        payload_cipher=CommandPayloadCipher(Fernet.generate_key()),
        grants=grants,
    )
    peer_key = Fernet.generate_key()
    clock = SimpleNamespace(now=NOW)
    api = PersonalCommandService(
        store, grants, peer_reference_key=peer_key, clock=lambda: clock.now
    )
    reference = await api.issue_peer_reference(
        principal=principal, delegation=delegation, peer=PeerIdentity("user", 42), now=NOW
    )
    yield SimpleNamespace(
        api=api,
        store=store,
        grants=grants,
        delegation=delegation,
        principal=principal,
        peer_ref=reference,
        grant_ref="grant_" + delegation.delegation_id.hex,
        clock=clock,
        sessions=sessions,
        peer_key=peer_key,
    )
    await engine.dispose()


async def prepare(service, key="intent-1", text="synthetic", **changes):
    arguments = dict(
        principal=service.principal,
        grant_ref=service.grant_ref,
        peer_ref=service.peer_ref,
        request_key=key,
        text=text,
    )
    arguments.update(changes)
    return await service.api.prepare(**arguments)


@pytest.mark.anyio
async def test_prepare_replay_queue_cancel_and_results_never_claim_delivery(service):
    assert await service.api.readiness(principal=service.principal) == {
        "ready": False,
        "runtime_connected": False,
        "blockers": ["runtime_dispatch_not_bound"],
    }
    first = await prepare(service)
    replay = await prepare(service)
    assert first["state"] == "prepared" and replay["replay"] is True
    assert replay["operation_ref"] == first["operation_ref"]
    assert set(first) == {
        "operation_ref",
        "state",
        "receipt_ref",
        "error_code",
        "replay",
        "sent",
        "runtime_connected",
    }
    queued = await service.api.execute(
        principal=service.principal, operation_ref=first["operation_ref"]
    )
    assert queued["state"] == "queued" and queued["sent"] is False
    assert queued["runtime_connected"] is False
    cancelled = await service.api.cancel(
        principal=service.principal, operation_ref=first["operation_ref"]
    )
    assert cancelled["state"] == "cancelled"
    assert (
        await service.api.result(principal=service.principal, operation_ref=first["operation_ref"])
        == cancelled
    )
    async with service.sessions.begin() as session:
        row = await session.scalar(select(RuntimeOperationRow))
        assert row.peer_key == "user:42"
        assert b"synthetic" not in row.payload_ciphertext
        assert "synthetic" not in json.dumps(first)


@pytest.mark.anyio
async def test_changed_payload_conflicts_but_new_key_is_distinct_intent(service):
    first = await prepare(service)
    with pytest.raises(CommandRuntimeError, match="request_key_payload_conflict"):
        await prepare(service, text="different")
    second = await prepare(service, key="intent-2")
    assert second["operation_ref"] != first["operation_ref"]


@pytest.mark.anyio
async def test_wrong_client_and_scope_fail_closed_for_existing_operation(service):
    first = await prepare(service)
    foreign_client = replace(service.principal, oauth_client_id="foreign-client")
    with pytest.raises(CommandRuntimeError, match="peer_reference_invalid"):
        await prepare(service, principal=foreign_client)
    for method in (service.api.execute, service.api.status, service.api.result, service.api.cancel):
        with pytest.raises(CommandRuntimeError):
            await method(principal=foreign_client, operation_ref=first["operation_ref"])
    for principal in (
        replace(service.principal, auth_method="bearer"),
        replace(service.principal, subject="foreign-owner"),
        replace(service.principal, oauth_client_id=None),
        replace(
            service.principal,
            access_scope=replace(service.principal.access_scope, account_ids=frozenset()),
        ),
        replace(
            service.principal,
            access_scope=replace(
                service.principal.access_scope, account_ids=frozenset({TELEGRAM_MARK_BY_ACCOUNT_ID})
            ),
        ),
        replace(
            service.principal,
            access_scope=replace(service.principal.access_scope, workspace_id="foreign-workspace"),
        ),
    ):
        with pytest.raises(CommandRuntimeError):
            await prepare(service, principal=principal)


@pytest.mark.anyio
async def test_revoked_grant_blocks_execute_but_preserves_same_client_receipts(service):
    first = await prepare(service)
    await service.grants.revoke(
        principal=service.principal,
        delegation_id=service.delegation.delegation_id,
        expected_revision=0,
        now=NOW,
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await service.api.execute(principal=service.principal, operation_ref=first["operation_ref"])
    result = await service.api.result(
        principal=service.principal, operation_ref=first["operation_ref"]
    )
    assert result["operation_ref"] == first["operation_ref"] and result["state"] == "prepared"


@pytest.mark.anyio
async def test_references_expire_are_grant_bound_and_reject_raw_peer_ids(service):
    with pytest.raises(CommandRuntimeError, match="peer_reference_invalid"):
        await prepare(service, peer_ref="user:42")
    with pytest.raises(CommandRuntimeError, match="peer_reference_invalid"):
        await prepare(service, grant_ref="grant_" + uuid4().hex)
    data = json.loads(Fernet(service.peer_key).decrypt(service.peer_ref[5:].encode()))
    data["provider_id"] = True
    tampered = "peer_" + Fernet(service.peer_key).encrypt(json.dumps(data).encode()).decode()
    with pytest.raises(CommandRuntimeError, match="peer_reference_invalid"):
        await prepare(service, peer_ref=tampered)
    service.clock.now = NOW + timedelta(minutes=5)
    with pytest.raises(CommandRuntimeError, match="peer_reference_expired"):
        await prepare(service)


@pytest.mark.anyio
@pytest.mark.parametrize("text", [True, {}, "", "x" * 4097])
async def test_text_is_bounded_plain_text(service, text):
    with pytest.raises(CommandRuntimeError, match="command_text_invalid"):
        await prepare(service, text=text)


@pytest.mark.anyio
async def test_reference_issuer_cannot_mint_unapproved_or_changed_delegation(service):
    for delegation in (
        replace(service.delegation, delegation_id=uuid4()),
        replace(service.delegation, capabilities=frozenset({"send_text", "delete"})),
    ):
        with pytest.raises(CommandRuntimeError):
            await service.api.issue_peer_reference(
                principal=service.principal,
                delegation=delegation,
                peer=PeerIdentity("user", 42),
                now=NOW,
            )
    with pytest.raises(CommandRuntimeError, match="command_scope_denied"):
        await service.api.issue_peer_reference(
            principal=service.principal,
            delegation=service.delegation,
            peer=PeerIdentity("user", 43),
            now=NOW,
        )


@pytest.mark.anyio
async def test_methods_require_separate_scopes_and_strict_operation_refs(service):
    no_read = replace(service.principal, scopes=frozenset({"gate:personal:send_text"}))
    with pytest.raises(CommandRuntimeError, match="command_scope_denied"):
        await service.api.readiness(principal=no_read)
    with pytest.raises(CommandRuntimeError, match="operation_reference_invalid"):
        await service.api.status(principal=service.principal, operation_ref=str(uuid4()))
    with pytest.raises(CommandRuntimeError, match="request_key_invalid"):
        await prepare(service, key=True)
