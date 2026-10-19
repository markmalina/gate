"""Staged durable RU grants; provisioning is an internal approved-setup port.

No MCP registration, production migration, credentials import or provider call.
The caller's transaction holds binding then grant locks through authorization.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Uuid,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mcp_security import Principal
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    personal_client_namespace,
)
from app.personal_telegram.command_store import PersonalRuntimeBase, RuntimeBindingRow


class RuntimeGrantRow(PersonalRuntimeBase):
    __tablename__ = "personal_command_grants"
    __table_args__ = (CheckConstraint("revision >= 0", name="ck_personal_grant_revision"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    binding_id: Mapped[UUID] = mapped_column(ForeignKey(RuntimeBindingRow.id), index=True)
    owner_subject: Mapped[str] = mapped_column(String(256))
    client_namespace: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[str] = mapped_column(String(64))
    approval_ref: Mapped[str] = mapped_column(String(128))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)


def _utc(value: datetime) -> datetime:
    # SQLite's stored timestamps are naive; public input clocks must be aware.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clock(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise CommandRuntimeError("timezone_required")


def _namespace(value: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise CommandRuntimeError("delegation_scope_invalid")


class SqlGrantRegistry:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: RuCommandScope,
        policy_key: bytes,
        key_version: str,
    ) -> None:
        scope.validate()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", key_version):
            raise CommandRuntimeError("delegation_key_invalid")
        try:
            self._cipher = Fernet(policy_key)
        except (TypeError, ValueError):
            raise CommandRuntimeError("delegation_key_invalid") from None
        self.sessions = sessions
        self.scope = scope
        self.key_version = key_version

    def _envelope(self, grant: CommandDelegation, approval_ref: str) -> dict[str, object]:
        grant.scope.validate()
        if grant.scope != self.scope or grant.revoked:
            raise CommandRuntimeError("delegation_scope_mismatch")
        _namespace(grant.client_namespace)
        _clock(grant.expires_at)
        if (
            not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", approval_ref)
            or not 1 <= len(grant.capabilities) <= 64
            or not 1 <= len(grant.peer_keys) <= 1024
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) for value in grant.capabilities)
        ):
            raise CommandRuntimeError("delegation_scope_invalid")
        for value in grant.peer_keys:
            try:
                kind, number = value.split(":")
                peer = PeerIdentity(kind, int(number))
                if peer.key != value:
                    raise ValueError
            except (ValueError, CommandRuntimeError):
                raise CommandRuntimeError("delegation_scope_invalid") from None
        return {
            "schema": "gate.personal-command-grant/v1",
            "grant": str(grant.delegation_id),
            "binding": str(self.scope.binding_id),
            "workspace": self.scope.workspace,
            "account": str(self.scope.account_id),
            "owner": self.scope.owner_subject,
            "client": grant.client_namespace,
            "expiry": _utc(grant.expires_at).isoformat(),
            "key_version": self.key_version,
            "approval_ref": approval_ref,
            "capabilities": sorted(grant.capabilities),
            "peers": sorted(grant.peer_keys),
        }

    async def _binding(self, session: AsyncSession) -> None:
        row = await session.scalar(
            select(RuntimeBindingRow)
            .where(
                RuntimeBindingRow.id == self.scope.binding_id,
                RuntimeBindingRow.workspace == self.scope.workspace,
                RuntimeBindingRow.account_id == str(self.scope.account_id),
                RuntimeBindingRow.owner_subject == self.scope.owner_subject,
            )
            .with_for_update()
        )
        if row is None or row.custody_state == "revoked":
            raise CommandRuntimeError("ru_binding_not_found")

    async def _row(
        self, session: AsyncSession, delegation_id: UUID, client_namespace: str
    ) -> RuntimeGrantRow:
        await self._binding(session)
        row = await session.scalar(
            select(RuntimeGrantRow)
            .where(
                RuntimeGrantRow.id == delegation_id,
                RuntimeGrantRow.binding_id == self.scope.binding_id,
                RuntimeGrantRow.owner_subject == self.scope.owner_subject,
                RuntimeGrantRow.client_namespace == client_namespace,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise CommandRuntimeError("delegation_not_found")
        return row

    def _decode(self, row: RuntimeGrantRow) -> CommandDelegation:
        # Scope/client row filtering MUST precede this decrypt.
        try:
            if row.key_version != self.key_version:
                raise ValueError
            data = json.loads(self._cipher.decrypt(row.policy_ciphertext))
            capabilities, peers = data["capabilities"], data["peers"]
            if (
                not isinstance(capabilities, list)
                or not isinstance(peers, list)
                or any(not isinstance(item, str) for item in capabilities + peers)
                or len(capabilities) != len(set(capabilities))
                or len(peers) != len(set(peers))
            ):
                raise ValueError
            grant = CommandDelegation(
                row.id,
                self.scope,
                row.client_namespace,
                frozenset(capabilities),
                frozenset(peers),
                _utc(row.expires_at),
            )
            if data != self._envelope(grant, row.approval_ref):
                raise ValueError
            return grant
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, CommandRuntimeError):
            raise CommandRuntimeError("delegation_policy_unavailable") from None

    async def install_approved_grant(
        self, delegation: CommandDelegation, *, approval_ref: str, now: datetime
    ) -> CommandDelegation:
        """Privileged setup only: caller must verify owner approval before invoking.

        An ID's scope, client, policy, expiry and approval reference are immutable.
        This method is intentionally not a tool or user-controlled self-grant API.
        """
        _clock(now)
        envelope = self._envelope(delegation, approval_ref)
        if _utc(delegation.expires_at) <= now:
            raise CommandRuntimeError("delegation_inactive")
        try:
            async with self.sessions.begin() as session:
                await self._binding(session)
                existing = await session.scalar(
                    select(RuntimeGrantRow)
                    .where(RuntimeGrantRow.id == delegation.delegation_id)
                    .with_for_update()
                )
                if existing is not None:
                    if (
                        existing.binding_id != self.scope.binding_id
                        or existing.owner_subject != self.scope.owner_subject
                        or existing.client_namespace != delegation.client_namespace
                    ):
                        raise CommandRuntimeError("delegation_policy_conflict")
                    saved = self._decode(existing)
                    if self._envelope(saved, existing.approval_ref) != envelope:
                        raise CommandRuntimeError("delegation_policy_conflict")
                    if existing.revoked:
                        raise CommandRuntimeError("delegation_inactive")
                    return saved
                session.add(
                    RuntimeGrantRow(
                        id=delegation.delegation_id,
                        binding_id=self.scope.binding_id,
                        owner_subject=self.scope.owner_subject,
                        client_namespace=delegation.client_namespace,
                        expires_at=delegation.expires_at,
                        policy_ciphertext=self._cipher.encrypt(
                            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
                        ),
                        key_version=self.key_version,
                        approval_ref=approval_ref,
                        revoked=False,
                        revision=0,
                    )
                )
                await session.flush()
                return delegation
        except SQLAlchemyError:
            raise CommandRuntimeError("delegation_store_unavailable") from None

    async def require(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        delegation_id: UUID,
        capability: str,
        peer: PeerIdentity,
        now: datetime,
    ) -> CommandDelegation:
        namespace = personal_client_namespace(principal)
        if (
            principal.subject != self.scope.owner_subject
            or principal.access_scope.workspace_id != self.scope.workspace
            or self.scope.account_id not in principal.access_scope.account_ids
            or f"gate:personal:{capability}" not in principal.scopes
        ):
            raise CommandRuntimeError("ru_scope_denied")
        grant = await self.for_operation(
            session,
            owner_subject=principal.subject,
            client_namespace=namespace,
            delegation_id=delegation_id,
            capability=capability,
            peer=peer,
            now=now,
        )
        grant.admit(principal=principal, capability=capability, peer=peer, now=now)
        return grant

    async def for_operation(
        self,
        session: AsyncSession,
        *,
        owner_subject: str,
        client_namespace: str,
        delegation_id: UUID,
        capability: str,
        peer: PeerIdentity,
        now: datetime,
    ) -> CommandDelegation:
        """Trusted worker rechecks a stored operation in the caller transaction."""
        _clock(now)
        _namespace(client_namespace)
        peer.validate()
        if owner_subject != self.scope.owner_subject:
            raise CommandRuntimeError("ru_scope_denied")
        try:
            row = await self._row(session, delegation_id, client_namespace)
            if row.revoked or _utc(row.expires_at) <= now:
                raise CommandRuntimeError("delegation_inactive")
            grant = self._decode(row)
            if capability not in grant.capabilities or peer.key not in grant.peer_keys:
                raise CommandRuntimeError("command_scope_denied")
            return grant
        except SQLAlchemyError:
            raise CommandRuntimeError("delegation_store_unavailable") from None

    async def revoke(
        self,
        *,
        principal: Principal,
        delegation_id: UUID,
        expected_revision: int,
        now: datetime,
    ) -> int:
        _clock(now)
        namespace = personal_client_namespace(principal)
        if (
            principal.subject != self.scope.owner_subject
            or principal.access_scope.workspace_id != self.scope.workspace
            or self.scope.account_id not in principal.access_scope.account_ids
        ):
            raise CommandRuntimeError("ru_scope_denied")
        if type(expected_revision) is not int or expected_revision < 0:
            raise CommandRuntimeError("delegation_revision_conflict")
        try:
            async with self.sessions.begin() as session:
                row = await self._row(session, delegation_id, namespace)
                if row.revision != expected_revision:
                    raise CommandRuntimeError("delegation_revision_conflict")
                revision = await session.scalar(
                    update(RuntimeGrantRow)
                    .where(
                        RuntimeGrantRow.id == row.id,
                        RuntimeGrantRow.revision == expected_revision,
                    )
                    .values(revoked=True, revision=expected_revision + 1)
                    .returning(RuntimeGrantRow.revision)
                )
                if revision is None:
                    raise CommandRuntimeError("delegation_revision_conflict")
                return revision
        except SQLAlchemyError:
            raise CommandRuntimeError("delegation_store_unavailable") from None
