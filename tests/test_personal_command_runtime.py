"""Provider-free lifecycle, persistence and idempotency acceptance tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import TELEGRAM_MARK_BY_ACCOUNT_ID, TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.cloud_worker import RuCloudWorker, TelegramApplication
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    personal_client_namespace,
)
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    PersonalRuntimeBase,
    RuntimeAttemptRow,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.grant_registry import SqlGrantRegistry
from app.personal_telegram.session_persistence import (
    EncryptedSessionRecord,
    SessionCipher,
    open_encrypted_session,
)
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 11, 21, tzinfo=UTC)
TTL = timedelta(seconds=30)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def runtime(tmp_path):
    scope = RuCommandScope("synthetic-workspace", TELEGRAM_MARK_RU_ACCOUNT_ID, uuid4(), "owner")
    cipher = SessionCipher(key=Fernet.generate_key(), key_version="test-v1")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
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
                ciphertext=cipher.seal(scope, session="", generation=1),
                revision=0,
                key_version=cipher.key_version,
                custody_state="prepared",
            )
        )
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
    delegation = CommandDelegation(
        uuid4(),
        scope,
        personal_client_namespace(principal),
        frozenset({"send_text"}),
        frozenset({"user:42", "channel:42"}),
        NOW + timedelta(hours=1),
    )
    grants = SqlGrantRegistry(
        sessions, scope=scope, policy_key=Fernet.generate_key(), key_version="grant-v1"
    )
    await grants.install_approved_grant(delegation, approval_ref="synthetic-approval", now=NOW)
    store = SqlCommandStore(
        sessions,
        scope=scope,
        payload_cipher=CommandPayloadCipher(Fernet.generate_key()),
        grants=grants,
    )
    yield SimpleNamespace(
        scope=scope,
        cipher=cipher,
        store=store,
        sessions=sessions,
        principal=principal,
        delegation=delegation,
        grants=grants,
    )
    await engine.dispose()


async def enqueue(runtime, key: str, *, peer: PeerIdentity | None = None, text: str = "synthetic"):
    prepared = await runtime.store.prepare(
        principal=runtime.principal,
        delegation=runtime.delegation,
        capability="send_text",
        peer=peer or PeerIdentity("user", 42),
        request_key=key,
        payload={"text": text},
        now=NOW,
    )
    return await runtime.store.execute(
        prepared.operation_id, principal=runtime.principal, delegation=runtime.delegation, now=NOW
    )


@pytest.mark.anyio
async def test_explicit_ru_boundary_rejects_by_and_workspace_fallback(runtime):
    with pytest.raises(CommandRuntimeError, match="ru_account_required"):
        replace(runtime.scope, account_id=TELEGRAM_MARK_BY_ACCOUNT_ID).validate()
    for principal in [
        replace(runtime.principal, auth_method="bearer"),
        replace(runtime.principal, subject="other"),
        replace(
            runtime.principal,
            access_scope=replace(runtime.principal.access_scope, account_ids=frozenset()),
        ),
        replace(
            runtime.principal,
            access_scope=replace(
                runtime.principal.access_scope, account_ids=frozenset({TELEGRAM_MARK_BY_ACCOUNT_ID})
            ),
        ),
    ]:
        with pytest.raises(CommandRuntimeError, match="ru_scope_denied"):
            runtime.delegation.admit(
                principal=principal, capability="send_text", peer=PeerIdentity("user", 42), now=NOW
            )


@pytest.mark.anyio
async def test_delegation_expiry_scope_and_revocation(runtime):
    for grant in [
        replace(runtime.delegation, revoked=True),
        replace(runtime.delegation, expires_at=NOW),
    ]:
        with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
            grant.admit(
                principal=runtime.principal,
                capability="send_text",
                peer=PeerIdentity("user", 42),
                now=NOW,
            )
    for capability, peer in [
        ("delete", PeerIdentity("user", 42)),
        ("send_text", PeerIdentity("chat", 42)),
    ]:
        with pytest.raises(CommandRuntimeError, match="command_scope_denied"):
            runtime.delegation.admit(
                principal=runtime.principal, capability=capability, peer=peer, now=NOW
            )


@pytest.mark.anyio
async def test_request_replay_and_independent_identical_intents(runtime):
    first = await enqueue(runtime, "intent-1")
    replay = await runtime.store.prepare(
        principal=runtime.principal,
        delegation=runtime.delegation,
        capability="send_text",
        peer=PeerIdentity("user", 42),
        request_key="intent-1",
        payload={"text": "synthetic"},
        now=NOW,
    )
    assert replay.operation_id == first.operation_id and replay.replay
    second = await enqueue(runtime, "intent-2")
    assert second.operation_id != first.operation_id
    with pytest.raises(CommandRuntimeError, match="request_key_payload_conflict"):
        await enqueue(runtime, "intent-1", text="different")


@pytest.mark.anyio
async def test_lease_expiry_never_authorizes_a_second_connection(runtime):
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    with pytest.raises(CommandRuntimeError, match="session_owner_unavailable"):
        await runtime.store.acquire_owner(now=NOW + TTL * 2, ttl=TTL)
    with pytest.raises(CommandRuntimeError, match="session_ownership_lost"):
        await runtime.store.renew_owner(token, now=NOW + TTL * 2, ttl=TTL, health="ready")
    await runtime.store.release_disconnected_owner(token)
    replacement = await runtime.store.acquire_owner(now=NOW + TTL * 2, ttl=TTL)
    assert replacement.owner != token.owner and replacement.generation == token.generation + 1
    with pytest.raises(CommandRuntimeError, match="session_ownership_lost"):
        await runtime.store.release_disconnected_owner(token)


@pytest.mark.anyio
async def test_durable_attempt_unknown_quarantines_only_exact_typed_peer(runtime):
    first = await enqueue(runtime, "intent-1")
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    command = await runtime.store.claim(token, now=NOW)
    assert command is not None and command.operation_id == first.operation_id
    await enqueue(runtime, "intent-2")
    other = await enqueue(runtime, "intent-3", peer=PeerIdentity("channel", 42))
    async with runtime.sessions() as session:
        attempt = await session.get(RuntimeAttemptRow, command.attempt_id)
        assert attempt is not None and attempt.state == "unknown"
        assert int(attempt.random_id) == command.random_id > 0
    await runtime.store.release_disconnected_owner(token)
    assert (
        await runtime.store.status(first.operation_id, principal=runtime.principal)
    ).state == "unknown"
    replacement = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(replacement, now=NOW, ttl=TTL, health="ready")
    independent = await runtime.store.claim(replacement, now=NOW)
    assert independent is not None and independent.operation_id == other.operation_id
    assert await runtime.store.claim(replacement, now=NOW) is None


@pytest.mark.anyio
async def test_receipt_replay_never_creates_a_second_attempt(runtime):
    operation = await enqueue(runtime, "intent-1")
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    command = await runtime.store.claim(token, now=NOW)
    assert command is not None
    with pytest.raises(CommandRuntimeError, match="command_receipt_required"):
        await runtime.store.finish(command, token, state="succeeded", now=NOW)
    await runtime.store.finish(
        command, token, state="succeeded", now=NOW, receipt_ref="synthetic-receipt"
    )
    replay = await runtime.store.execute(
        operation.operation_id, principal=runtime.principal, delegation=runtime.delegation, now=NOW
    )
    assert replay.state == "succeeded" and replay.receipt_ref == "synthetic-receipt"
    assert await runtime.store.claim(token, now=NOW) is None
    async with runtime.sessions() as session:
        assert len((await session.scalars(select(RuntimeAttemptRow))).all()) == 1


@pytest.mark.anyio
async def test_cancel_after_dispatch_is_unknown_and_late_ack_is_preserved(runtime):
    operation = await enqueue(runtime, "intent-1")
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    command = await runtime.store.claim(token, now=NOW)
    assert command is not None
    assert (
        await runtime.store.cancel(operation.operation_id, principal=runtime.principal, now=NOW)
    ).state == "unknown"
    assert (
        await runtime.store.finish(
            command, token, state="succeeded", now=NOW, receipt_ref="late-receipt"
        )
    ).state == "succeeded"


@pytest.mark.anyio
async def test_cancel_before_dispatch_has_zero_attempts(runtime):
    operation = await enqueue(runtime, "intent-1")
    assert (
        await runtime.store.cancel(operation.operation_id, principal=runtime.principal, now=NOW)
    ).state == "cancelled"
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    assert await runtime.store.claim(token, now=NOW) is None


class MemoryRepository:
    def __init__(self, record):
        self.record = record
        self.fail = False

    async def load(self, scope):
        return self.record

    async def compare_and_swap(self, scope, *, expected_revision, ciphertext, key_version, state):
        if self.fail:
            raise RuntimeError("synthetic-sensitive-provider-detail")
        assert expected_revision == self.record.revision
        self.record = EncryptedSessionRecord(ciphertext, expected_revision + 1, key_version, state)
        return self.record.revision


@pytest.mark.anyio
async def test_actual_telethon_callback_roundtrip_dc_change_close_and_transient_clone(runtime):
    from telethon import TelegramClient
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    repo = MemoryRepository(await runtime.store.load(runtime.scope))
    session = await open_encrypted_session(
        scope=runtime.scope, generation=1, cipher=runtime.cipher, repository=repo
    )
    session.set_dc(2, "149.154.167.51", 443)
    client = TelegramClient(session, 123, "a" * 32, receive_updates=False)
    await client._auth_key_callback(AuthKey(bytes(range(256))))
    serialized = runtime.cipher.unseal(
        runtime.scope, ciphertext=repo.record.ciphertext, generation=1
    )
    restored = StringSession(serialized)
    assert restored.dc_id == 2 and restored.auth_key.key == bytes(range(256))
    session.set_dc(4, "149.154.167.91", 443)
    await session.close()
    restored = StringSession(
        runtime.cipher.unseal(runtime.scope, ciphertext=repo.record.ciphertext, generation=1)
    )
    assert restored.dc_id == 4 and repo.record.revision == 2
    clone = session.clone()
    clone.set_dc(3, "149.154.175.100", 443)
    clone.auth_key = AuthKey(b"x" * 256)
    clone.save()
    assert repo.record.revision == 2
    with pytest.raises(CommandRuntimeError, match="explicit_session_revoke_required"):
        await session.delete()


@pytest.mark.anyio
async def test_session_failure_poisoning_redaction_and_scope_tamper(runtime):
    repo = MemoryRepository(await runtime.store.load(runtime.scope))
    session = await open_encrypted_session(
        scope=runtime.scope, generation=1, cipher=runtime.cipher, repository=repo
    )
    repo.fail = True
    for _ in range(2):
        with pytest.raises(CommandRuntimeError, match="^session_persistence_failed$"):
            await session.save()
    assert repo.record.revision == 0
    for scope, generation in [(replace(runtime.scope, workspace="foreign"), 1), (runtime.scope, 2)]:
        with pytest.raises(CommandRuntimeError, match="session_envelope_unavailable"):
            runtime.cipher.unseal(scope, ciphertext=repo.record.ciphertext, generation=generation)


class FakeClient:
    def __init__(self, stop, *, owner=42, authorized=True, disconnect_fail=False):
        self.stop = stop
        self.owner = owner
        self.authorized = authorized
        self.disconnect_fail = disconnect_fail
        self.connected = False
        self.connect_count = 0

    async def connect(self):
        self.connect_count += 1
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def owner_id(self):
        self.stop.set()
        return self.owner

    async def disconnect(self):
        if self.disconnect_fail:
            raise RuntimeError("synthetic-sensitive-disconnect-detail")
        self.connected = False

    def is_connected(self):
        return self.connected


def worker(runtime, client, *, enabled=True):
    return RuCloudWorker(
        store=runtime.store,
        cipher=runtime.cipher,
        session_generation=1,
        expected_provider_user_id=42,
        client_factory=lambda session: client,
        enabled=enabled,
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_worker_default_off_constructs_no_client_and_no_lease(runtime):
    stop = asyncio.Event()
    client = FakeClient(stop)
    assert (await worker(runtime, client, enabled=False).run_until(stop)).state == "disabled"
    assert client.connect_count == 0
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.release_disconnected_owner(token)
    assert "a" * 32 not in repr(TelegramApplication(123, "a" * 32))


@pytest.mark.anyio
async def test_worker_verified_connection_and_restart(runtime):
    for _ in range(2):
        stop = asyncio.Event()
        client = FakeClient(stop)
        result = await worker(runtime, client).run_until(stop)
        assert result.state == "disconnected" and client.connect_count == 1
        assert not client.connected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "owner,authorized,code",
    [
        (99, True, "ru_session_owner_mismatch"),
        (42, False, "ru_session_login_required"),
    ],
)
async def test_worker_rejects_wrong_owner_or_revoked_session(runtime, owner, authorized, code):
    stop = asyncio.Event()
    client = FakeClient(stop, owner=owner, authorized=authorized)
    result = await worker(runtime, client).run_until(stop)
    assert result.code == code and not client.connected
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.release_disconnected_owner(token)


@pytest.mark.anyio
async def test_worker_disconnect_failure_latches_ownership(runtime):
    stop = asyncio.Event()
    client = FakeClient(stop, disconnect_fail=True)
    result = await worker(runtime, client).run_until(stop)
    assert result.code == "session_disconnect_unconfirmed" and client.connected
    with pytest.raises(CommandRuntimeError, match="session_owner_unavailable"):
        await runtime.store.acquire_owner(now=NOW + timedelta(hours=1), ttl=TTL)


@pytest.mark.anyio
async def test_session_cancelled_commit_is_poisoned_and_preserves_cancellation(runtime):
    repo = MemoryRepository(await runtime.store.load(runtime.scope))
    session = await open_encrypted_session(
        scope=runtime.scope, generation=1, cipher=runtime.cipher, repository=repo
    )

    async def cancelled_commit(*args, **kwargs):
        raise asyncio.CancelledError

    repo.compare_and_swap = cancelled_commit
    with pytest.raises(asyncio.CancelledError):
        await session.save()
    with pytest.raises(CommandRuntimeError, match="session_persistence_failed"):
        await session.save()


@pytest.mark.anyio
async def test_session_concurrent_saves_serialize_and_invalid_format_is_redacted(runtime):
    repo = MemoryRepository(await runtime.store.load(runtime.scope))
    session = await open_encrypted_session(
        scope=runtime.scope, generation=1, cipher=runtime.cipher, repository=repo
    )
    await asyncio.gather(*(session.save() for _ in range(5)))
    assert repo.record.revision == 5 and repo.record.state == "transition"
    repo.record = EncryptedSessionRecord(
        runtime.cipher.seal(runtime.scope, session="1AA", generation=1),
        6,
        runtime.cipher.key_version,
        "prepared",
    )
    with pytest.raises(CommandRuntimeError, match="session_format_invalid"):
        await open_encrypted_session(
            scope=runtime.scope, generation=1, cipher=runtime.cipher, repository=repo
        )


@pytest.mark.anyio
async def test_worker_lost_database_before_ready_disconnects_without_dispatch(runtime, monkeypatch):
    original = runtime.store.renew_owner

    async def failed_renew(token, *, now, ttl, health):
        if health == "ready":
            raise RuntimeError("synthetic-sensitive-db-detail")
        await original(token, now=now, ttl=ttl, health=health)

    monkeypatch.setattr(runtime.store, "renew_owner", failed_renew)
    stop = asyncio.Event()
    client = FakeClient(stop)
    result = await worker(runtime, client).run_until(stop)
    assert result.code == "telegram_runtime_failed" and not client.connected
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.release_disconnected_owner(token)


@pytest.mark.anyio
async def test_second_worker_never_constructs_a_client(runtime):
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    client = FakeClient(asyncio.Event())
    with pytest.raises(CommandRuntimeError, match="session_owner_unavailable"):
        await worker(runtime, client).run_until(client.stop)
    assert client.connect_count == 0
    await runtime.store.release_disconnected_owner(token)


@pytest.mark.anyio
async def test_execute_rechecks_revocation_and_expiry_before_queueing(runtime):
    operation = await runtime.store.prepare(
        principal=runtime.principal,
        delegation=runtime.delegation,
        capability="send_text",
        peer=PeerIdentity("user", 42),
        request_key="intent-1",
        payload={"text": "synthetic"},
        now=NOW,
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await runtime.store.execute(
            operation.operation_id,
            principal=runtime.principal,
            delegation=replace(runtime.delegation, revoked=True),
            now=NOW,
        )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await runtime.store.execute(
            operation.operation_id,
            principal=runtime.principal,
            delegation=runtime.delegation,
            now=runtime.delegation.expires_at,
        )
    assert (
        await runtime.store.status(operation.operation_id, principal=runtime.principal)
    ).state == "prepared"


@pytest.mark.anyio
async def test_durable_revocation_denies_queue_and_worker_but_preserves_owner_receipt(runtime):
    operation = await enqueue(runtime, "revocation-intent")
    await runtime.grants.revoke(
        principal=runtime.principal,
        delegation_id=runtime.delegation.delegation_id,
        expected_revision=0,
        now=NOW,
    )
    with pytest.raises(CommandRuntimeError, match="delegation_inactive"):
        await runtime.store.execute(operation.operation_id, principal=runtime.principal, now=NOW)
    token = await runtime.store.acquire_owner(now=NOW, ttl=TTL)
    await runtime.store.renew_owner(token, now=NOW, ttl=TTL, health="ready")
    assert await runtime.store.claim(token, now=NOW) is None
    status = await runtime.store.status(operation.operation_id, principal=runtime.principal)
    assert status.state == "rejected" and status.error_code == "delegation_inactive"
    async with runtime.sessions() as session:
        assert not (await session.scalars(select(RuntimeAttemptRow))).all()


@pytest.mark.anyio
async def test_caller_modified_grant_policy_has_no_authority(runtime):
    with pytest.raises(CommandRuntimeError, match="delegation_policy_conflict"):
        await runtime.store.prepare(
            principal=runtime.principal,
            delegation=replace(runtime.delegation, expires_at=NOW + timedelta(days=1)),
            capability="send_text",
            peer=PeerIdentity("user", 42),
            request_key="tampered-grant",
            payload={"text": "synthetic"},
            now=NOW,
        )


@pytest.mark.anyio
async def test_operation_read_and_cancel_are_bound_to_verified_client(runtime):
    operation = await enqueue(runtime, "client-bound-intent")
    foreign = replace(runtime.principal, oauth_client_id="other-work-client")
    for action in [
        lambda: runtime.store.status(operation.operation_id, principal=foreign),
        lambda: runtime.store.cancel(operation.operation_id, principal=foreign, now=NOW),
        lambda: runtime.store.execute(operation.operation_id, principal=foreign, now=NOW),
    ]:
        with pytest.raises(CommandRuntimeError, match="client_scope_denied"):
            await action()
    assert (
        await runtime.store.status(operation.operation_id, principal=runtime.principal)
    ).state == "queued"


@pytest.mark.anyio
async def test_unbound_grant_registry_cannot_prepare(runtime):
    store = SqlCommandStore(
        runtime.sessions,
        scope=runtime.scope,
        payload_cipher=CommandPayloadCipher(Fernet.generate_key()),
    )
    with pytest.raises(CommandRuntimeError, match="delegation_registry_unbound"):
        await store.prepare(
            principal=runtime.principal,
            delegation=runtime.delegation,
            capability="send_text",
            peer=PeerIdentity("user", 42),
            request_key="unbound",
            payload={"text": "synthetic"},
            now=NOW,
        )
