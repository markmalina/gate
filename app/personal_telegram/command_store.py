"""Durable RU session/ownership and operation journal, with short transactions.

These additive tables are intentionally isolated from the production Alembic
head until the reviewed custody/migration operation is available. No automatic
create_all, migration, credentials import or Telegram effect occurs here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column

from app.core.mcp_security import Principal
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    command_fingerprint,
    require_request_key,
)
from app.personal_telegram.session_persistence import EncryptedSessionRecord


class PersonalRuntimeBase(DeclarativeBase):
    """Staged schema; activation requires an explicit reviewed migration package."""


class RuntimeBindingRow(PersonalRuntimeBase):
    __tablename__ = "personal_command_bindings"
    __table_args__ = (
        CheckConstraint(
            f"account_id = '{TELEGRAM_MARK_RU_ACCOUNT_ID}'", name="ck_personal_command_ru_only"
        ),
        UniqueConstraint("workspace", "account_id", name="uq_personal_command_workspace_ru"),
        CheckConstraint(
            "revision >= 0 AND session_generation > 0 AND lease_generation >= 0",
            name="ck_personal_command_generations",
        ),
        CheckConstraint(
            "custody_state IN ('prepared','transition','revoked')",
            name="ck_personal_command_custody_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(36))
    owner_subject: Mapped[str] = mapped_column(String(256))
    session_generation: Mapped[int] = mapped_column(Integer)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    revision: Mapped[int] = mapped_column(Integer)
    key_version: Mapped[str] = mapped_column(String(64))
    custody_state: Mapped[str] = mapped_column(String(32))
    lease_owner: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_state: Mapped[str] = mapped_column(String(32), default="disconnected")


class RuntimeOperationRow(PersonalRuntimeBase):
    __tablename__ = "personal_command_operations"
    __table_args__ = (
        Index("ix_personal_command_peer_state", "binding_id", "peer_key", "state"),
        Index("ix_personal_command_queue", "binding_id", "state", "available_at", "created_at"),
        UniqueConstraint(
            "binding_id",
            "owner_subject",
            "client_namespace",
            "request_key",
            name="uq_personal_command_intent",
        ),
        CheckConstraint(
            "state IN ('prepared','queued','running','succeeded','rejected',"
            "'cancelled','expired','unknown')",
            name="ck_personal_command_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    binding_id: Mapped[UUID] = mapped_column(ForeignKey(RuntimeBindingRow.id), index=True)
    owner_subject: Mapped[str] = mapped_column(String(256))
    client_namespace: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(64))
    peer_key: Mapped[str] = mapped_column(String(64))
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    delegation_id: Mapped[UUID] = mapped_column(Uuid)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class RuntimeAttemptRow(PersonalRuntimeBase):
    __tablename__ = "personal_command_attempts"
    __table_args__ = (UniqueConstraint("operation_id", name="uq_personal_command_one_attempt"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(ForeignKey(RuntimeOperationRow.id))
    lease_owner: Mapped[UUID] = mapped_column(Uuid)
    lease_generation: Mapped[int] = mapped_column(Integer)
    random_id: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True, slots=True)
class LeaseToken:
    owner: UUID
    generation: int


@dataclass(frozen=True, slots=True)
class OperationStatus:
    operation_id: UUID
    state: str
    receipt_ref: str | None
    error_code: str | None
    replay: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class ClaimedCommand:
    operation_id: UUID
    attempt_id: UUID
    capability: str
    peer_key: str
    random_id: int
    payload: dict[str, Any]
    delegation_id: UUID
    client_namespace: str
    expires_at: datetime


class CommandPayloadCipher:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def seal(self, scope: RuCommandScope, operation_id: UUID, payload: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(
            json.dumps(
                {
                    "schema": "gate.personal-command-payload/v1",
                    "binding": str(scope.binding_id),
                    "operation": str(operation_id),
                    "payload": payload,
                },
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        )

    def unseal(
        self, scope: RuCommandScope, operation_id: UUID, ciphertext: bytes
    ) -> dict[str, Any]:
        try:
            data = json.loads(self._fernet.decrypt(ciphertext))
            if (
                data.get("schema") != "gate.personal-command-payload/v1"
                or data.get("binding") != str(scope.binding_id)
                or data.get("operation") != str(operation_id)
                or not isinstance(data.get("payload"), dict)
            ):
                raise ValueError
            return dict(data["payload"])
        except (InvalidToken, ValueError, TypeError, UnicodeError, AttributeError):
            raise CommandRuntimeError("command_payload_unavailable") from None


class SqlCommandStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: RuCommandScope,
        payload_cipher: CommandPayloadCipher,
    ) -> None:
        scope.validate()
        self.sessions = sessions
        self.scope = scope
        self._cipher = payload_cipher

    async def _binding(self, session: AsyncSession) -> RuntimeBindingRow:
        row = await session.scalar(
            select(RuntimeBindingRow)
            .where(
                RuntimeBindingRow.id == self.scope.binding_id,
                RuntimeBindingRow.account_id == str(self.scope.account_id),
                RuntimeBindingRow.workspace == self.scope.workspace,
                RuntimeBindingRow.owner_subject == self.scope.owner_subject,
            )
            .with_for_update()
        )
        if row is None:
            raise CommandRuntimeError("ru_binding_not_found")
        return row

    async def load(self, scope: RuCommandScope) -> EncryptedSessionRecord:
        if scope != self.scope:
            raise CommandRuntimeError("ru_scope_denied")
        async with self.sessions.begin() as session:
            row = await self._binding(session)
            return EncryptedSessionRecord(
                row.ciphertext,
                row.revision,
                row.key_version,
                row.custody_state,
            )

    async def compare_and_swap(
        self,
        scope: RuCommandScope,
        *,
        expected_revision: int,
        ciphertext: bytes,
        key_version: str,
        state: str,
    ) -> int:
        if scope != self.scope or state not in {"prepared", "transition"}:
            raise CommandRuntimeError("session_scope_denied")
        async with self.sessions.begin() as session:
            row = await self._binding(session)
            if row.revision != expected_revision or row.custody_state == "revoked":
                raise CommandRuntimeError("session_revision_conflict")
            revision = await session.scalar(
                update(RuntimeBindingRow)
                .where(
                    RuntimeBindingRow.id == row.id,
                    RuntimeBindingRow.revision == expected_revision,
                    RuntimeBindingRow.custody_state != "revoked",
                )
                .values(
                    ciphertext=ciphertext,
                    key_version=key_version,
                    custody_state=state,
                    revision=expected_revision + 1,
                )
                .returning(RuntimeBindingRow.revision)
            )
            if revision is None:
                raise CommandRuntimeError("session_revision_conflict")
            return revision

    async def acquire_owner(self, *, now: datetime, ttl: timedelta) -> LeaseToken:
        _require_clock(now, ttl)
        async with self.sessions.begin() as session:
            row = await self._binding(session)
            # Expiry is not proof that the old TCP owner has stopped.
            if row.lease_owner is not None or row.custody_state != "prepared":
                raise CommandRuntimeError("session_owner_unavailable")
            owner = uuid4()
            generation = await session.scalar(
                update(RuntimeBindingRow)
                .where(
                    RuntimeBindingRow.id == row.id,
                    RuntimeBindingRow.lease_owner.is_(None),
                    RuntimeBindingRow.custody_state == "prepared",
                )
                .values(
                    lease_owner=owner,
                    lease_generation=RuntimeBindingRow.lease_generation + 1,
                    lease_expires_at=now + ttl,
                    health_state="connecting",
                )
                .returning(RuntimeBindingRow.lease_generation)
            )
            if generation is None:
                raise CommandRuntimeError("session_owner_unavailable")
            return LeaseToken(owner, generation)

    @staticmethod
    def _require_owner(row: RuntimeBindingRow, token: LeaseToken, now: datetime) -> None:
        if now.tzinfo is None:
            raise CommandRuntimeError("lease_clock_invalid")
        if (
            row.lease_owner != token.owner
            or row.lease_generation != token.generation
            or row.lease_expires_at is None
            or _utc(row.lease_expires_at) <= now
            or row.custody_state == "revoked"
        ):
            raise CommandRuntimeError("session_ownership_lost")

    async def renew_owner(
        self,
        token: LeaseToken,
        *,
        now: datetime,
        ttl: timedelta,
        health: str,
    ) -> None:
        _require_clock(now, ttl)
        if health not in {"connecting", "ready", "paused", "draining"}:
            raise CommandRuntimeError("health_state_invalid")
        async with self.sessions.begin() as session:
            row = await self._binding(session)
            self._require_owner(row, token, now)
            if health == "ready" and row.custody_state != "prepared":
                raise CommandRuntimeError("session_transition_not_ready")
            row.lease_expires_at = now + ttl
            row.health_state = health

    async def release_disconnected_owner(self, token: LeaseToken) -> None:
        """Trusted worker calls ONLY after disconnect completed successfully."""
        async with self.sessions.begin() as session:
            row = await self._binding(session)
            if row.lease_owner != token.owner or row.lease_generation != token.generation:
                raise CommandRuntimeError("session_ownership_lost")
            row.lease_owner = None
            row.lease_expires_at = None
            row.health_state = "disconnected"
            # A dead owner cannot prove what dispatched requests did.
            active = (
                await session.scalars(
                    select(RuntimeOperationRow).where(
                        RuntimeOperationRow.binding_id == self.scope.binding_id,
                        RuntimeOperationRow.state == "running",
                    )
                )
            ).all()
            for operation in active:
                operation.state = "unknown"
                operation.error_code = "owner_stopped_after_dispatch"

    async def prepare(
        self,
        *,
        principal: Principal,
        delegation: CommandDelegation,
        capability: str,
        peer: PeerIdentity,
        request_key: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> OperationStatus:
        if delegation.scope != self.scope or not delegation.client_namespace:
            raise CommandRuntimeError("delegation_scope_mismatch")
        delegation.admit(principal=principal, capability=capability, peer=peer, now=now)
        require_request_key(request_key)
        fingerprint = command_fingerprint(
            scope=self.scope,
            namespace=delegation.client_namespace,
            capability=capability,
            peer=peer,
            payload=payload,
            delegation_id=delegation.delegation_id,
        )
        try:
            async with self.sessions.begin() as session:
                await self._binding(session)
                row = await session.scalar(
                    select(RuntimeOperationRow).where(
                        RuntimeOperationRow.binding_id == self.scope.binding_id,
                        RuntimeOperationRow.owner_subject == principal.subject,
                        RuntimeOperationRow.client_namespace == delegation.client_namespace,
                        RuntimeOperationRow.request_key == request_key,
                    )
                )
                if row is not None:
                    return _replay(row, fingerprint)
                operation_id = uuid4()
                row = RuntimeOperationRow(
                    id=operation_id,
                    binding_id=self.scope.binding_id,
                    owner_subject=principal.subject,
                    client_namespace=delegation.client_namespace,
                    request_key=request_key,
                    fingerprint=fingerprint,
                    capability=capability,
                    peer_key=peer.key,
                    payload_ciphertext=self._cipher.seal(
                        self.scope,
                        operation_id,
                        payload,
                    ),
                    delegation_id=delegation.delegation_id,
                    expires_at=delegation.expires_at,
                    created_at=now,
                    available_at=now,
                    state="prepared",
                )
                session.add(row)
                await session.flush()
                return _status(row)
        except IntegrityError:
            async with self.sessions.begin() as session:
                await self._binding(session)
                row = await session.scalar(
                    select(RuntimeOperationRow).where(
                        RuntimeOperationRow.binding_id == self.scope.binding_id,
                        RuntimeOperationRow.owner_subject == principal.subject,
                        RuntimeOperationRow.client_namespace == delegation.client_namespace,
                        RuntimeOperationRow.request_key == request_key,
                    )
                )
                if row is None:
                    raise CommandRuntimeError("command_store_conflict") from None
                return _replay(row, fingerprint)

    async def _operation(self, session: AsyncSession, operation_id: UUID) -> RuntimeOperationRow:
        await self._binding(session)
        row = await session.scalar(
            select(RuntimeOperationRow)
            .where(
                RuntimeOperationRow.id == operation_id,
                RuntimeOperationRow.binding_id == self.scope.binding_id,
                RuntimeOperationRow.owner_subject == self.scope.owner_subject,
            )
            .with_for_update()
        )
        if row is None:
            raise CommandRuntimeError("command_not_found")
        return row

    async def execute(
        self,
        operation_id: UUID,
        *,
        principal: Principal,
        delegation: CommandDelegation,
        now: datetime,
    ) -> OperationStatus:
        async with self.sessions.begin() as session:
            row = await self._operation(session, operation_id)
            peer_type, provider_id = row.peer_key.split(":")
            if delegation.scope != self.scope or delegation.delegation_id != row.delegation_id:
                raise CommandRuntimeError("delegation_scope_mismatch")
            delegation.admit(
                principal=principal,
                capability=row.capability,
                peer=PeerIdentity(peer_type, int(provider_id)),
                now=now,
            )
            if _utc(row.expires_at) <= now:
                raise CommandRuntimeError("command_expired")
            if row.state == "prepared":
                row.state = "queued"
            return _status(row)

    async def status(self, operation_id: UUID) -> OperationStatus:
        async with self.sessions.begin() as session:
            return _status(await self._operation(session, operation_id))

    async def cancel(self, operation_id: UUID) -> OperationStatus:
        async with self.sessions.begin() as session:
            row = await self._operation(session, operation_id)
            if row.state in {"prepared", "queued"}:
                row.state = "cancelled"
            elif row.state == "running":
                # Cancellation cannot retract an already dispatched RPC.
                row.state = "unknown"
                row.error_code = "cancelled_after_dispatch"
            return _status(row)

    async def claim(self, token: LeaseToken, *, now: datetime) -> ClaimedCommand | None:
        """Reserve durably, NOT authorize dispatch. V2 fresh grant/guard is mandatory."""
        async with self.sessions.begin() as session:
            binding = await self._binding(session)
            self._require_owner(binding, token, now)
            if binding.health_state != "ready":
                raise CommandRuntimeError("runtime_not_ready")
            quarantine = aliased(RuntimeOperationRow)
            blocked_peer = (
                select(quarantine.id)
                .where(
                    quarantine.binding_id == RuntimeOperationRow.binding_id,
                    quarantine.peer_key == RuntimeOperationRow.peer_key,
                    quarantine.state.in_(("running", "unknown")),
                )
                .exists()
            )
            row = await session.scalar(
                select(RuntimeOperationRow)
                .where(
                    RuntimeOperationRow.binding_id == self.scope.binding_id,
                    RuntimeOperationRow.state == "queued",
                    RuntimeOperationRow.available_at <= now,
                    ~blocked_peer,
                )
                .order_by(RuntimeOperationRow.created_at, RuntimeOperationRow.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if _utc(row.expires_at) <= now:
                row.state = "expired"
                return None
            # UNKNOWN quarantines only this typed peer, not unrelated chats.
            payload = self._cipher.unseal(self.scope, row.id, row.payload_ciphertext)
            attempt_id = uuid4()
            random_id = int.from_bytes(hashlib.sha256(row.id.bytes).digest()[:8], "big")
            random_id = (random_id & ((1 << 63) - 1)) or 1
            session.add(
                RuntimeAttemptRow(
                    id=attempt_id,
                    operation_id=row.id,
                    lease_owner=token.owner,
                    lease_generation=token.generation,
                    random_id=str(random_id),
                    state="unknown",
                    started_at=now,
                )
            )
            row.state = "running"
            row.attempt_id = attempt_id
            # Transaction exits before caller can perform provider I/O.
            return ClaimedCommand(
                row.id,
                attempt_id,
                row.capability,
                row.peer_key,
                random_id,
                payload,
                row.delegation_id,
                row.client_namespace,
                _utc(row.expires_at),
            )

    async def finish(
        self,
        command: ClaimedCommand,
        token: LeaseToken,
        *,
        state: str,
        now: datetime,
        receipt_ref: str | None = None,
        error_code: str | None = None,
    ) -> OperationStatus:
        if state not in {"succeeded", "rejected", "unknown"}:
            raise CommandRuntimeError("command_outcome_invalid")
        if state == "succeeded" and not receipt_ref:
            raise CommandRuntimeError("command_receipt_required")
        if (
            error_code is not None
            and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
            or receipt_ref is not None
            and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", receipt_ref)
        ):
            raise CommandRuntimeError("command_outcome_invalid")
        async with self.sessions.begin() as session:
            binding = await self._binding(session)
            self._require_owner(binding, token, now)
            row = await self._operation(session, command.operation_id)
            attempt = await session.get(RuntimeAttemptRow, command.attempt_id)
            if (
                row.attempt_id != command.attempt_id
                or attempt is None
                or attempt.lease_owner != token.owner
                or attempt.lease_generation != token.generation
                or row.state not in {"running", "unknown"}
            ):
                raise CommandRuntimeError("command_attempt_conflict")
            row.state = state
            row.receipt_ref = receipt_ref
            row.error_code = error_code
            attempt.state = state
            return _status(row)


def _require_clock(now: datetime, ttl: timedelta) -> None:
    if now.tzinfo is None or not timedelta(seconds=5) <= ttl <= timedelta(minutes=5):
        raise CommandRuntimeError("lease_clock_invalid")


def _utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata; production PostgreSQL does not.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _status(row: RuntimeOperationRow, *, replay: bool = False) -> OperationStatus:
    return OperationStatus(row.id, row.state, row.receipt_ref, row.error_code, replay)


def _replay(row: RuntimeOperationRow, fingerprint: str) -> OperationStatus:
    if row.fingerprint != fingerprint:
        raise CommandRuntimeError("request_key_payload_conflict")
    return _status(row, replay=True)
