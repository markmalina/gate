"""Public, provider-free commands with opaque server-issued references.

This service journals and queues intents. It does not bind or invoke a Telegram
dispatcher; a queued operation is never evidence of a delivered message.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.core.mcp_security import Principal
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    personal_client_namespace,
    require_request_key,
    utc_now,
)
from app.personal_telegram.command_store import OperationStatus, SqlCommandStore
from app.personal_telegram.grant_registry import SqlGrantRegistry


class PersonalCommandService:
    """Bound to one immutable RU installation and one durable grant registry."""

    def __init__(
        self,
        store: SqlCommandStore,
        grants: SqlGrantRegistry,
        *,
        peer_reference_key: bytes,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if store.scope != grants.scope or store.sessions is not grants.sessions:
            raise CommandRuntimeError("delegation_scope_mismatch")
        try:
            self._peer_cipher = Fernet(peer_reference_key)
        except (TypeError, ValueError):
            raise CommandRuntimeError("peer_reference_key_invalid") from None
        self.store = store
        self.grants = grants
        self.scope = store.scope
        self._clock = clock

    def _principal(self, principal: Principal, scope: str) -> str:
        self.scope.validate()
        if (
            principal.auth_method != "oauth2"
            or principal.subject != self.scope.owner_subject
            or principal.access_scope.workspace_id != self.scope.workspace
            or self.scope.account_id not in principal.access_scope.account_ids
        ):
            raise CommandRuntimeError("ru_scope_denied")
        namespace = personal_client_namespace(principal)
        if scope not in principal.scopes:
            raise CommandRuntimeError("command_scope_denied")
        return namespace

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise CommandRuntimeError("timezone_required")
        return now

    async def readiness(self, *, principal: Principal) -> dict[str, Any]:
        self._principal(principal, "gate:personal:jobs:read")
        return {
            "ready": False,
            "runtime_connected": False,
            "blockers": ["runtime_dispatch_not_bound"],
        }

    async def issue_peer_reference(
        self,
        *,
        principal: Principal,
        delegation: CommandDelegation,
        peer: PeerIdentity,
        now: datetime,
    ) -> str:
        """Trusted resolver/setup only; deliberately absent from the MCP catalogue.

        Even this internal issuer checks the durable approved policy. Possessing
        a reference does not bypass fresh grant validation at prepare/execute.
        """
        namespace = self._principal(principal, "gate:personal:send_text")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise CommandRuntimeError("timezone_required")
        peer.validate()
        async with self.store.sessions.begin() as session:
            approved = await self.grants.require(
                session,
                principal=principal,
                delegation_id=delegation.delegation_id,
                capability="send_text",
                peer=peer,
                now=now,
            )
            if approved != delegation:
                raise CommandRuntimeError("delegation_policy_conflict")
        envelope = {
            "schema": "gate.personal-peer-reference/v1",
            "binding": str(self.scope.binding_id),
            "workspace": self.scope.workspace,
            "account": str(self.scope.account_id),
            "owner": self.scope.owner_subject,
            "client": namespace,
            "grant": str(approved.delegation_id),
            "peer_type": peer.peer_type,
            "provider_id": peer.provider_id,
            "expires_at": min(now + timedelta(minutes=5), approved.expires_at).isoformat(),
        }
        return "peer_" + self._peer_cipher.encrypt(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).decode("ascii")

    def _peer(
        self,
        reference: str,
        *,
        namespace: str,
        delegation_id: UUID,
        now: datetime,
    ) -> PeerIdentity:
        if not isinstance(reference, str) or not reference.startswith("peer_"):
            raise CommandRuntimeError("peer_reference_invalid")
        if not 100 <= len(reference) <= 4096:
            raise CommandRuntimeError("peer_reference_invalid")
        try:
            data = json.loads(self._peer_cipher.decrypt(reference[5:].encode("ascii")))
            peer = PeerIdentity(data["peer_type"], data["provider_id"])
            peer.validate()
            expires = datetime.fromisoformat(data["expires_at"])
            expected = {
                "schema": "gate.personal-peer-reference/v1",
                "binding": str(self.scope.binding_id),
                "workspace": self.scope.workspace,
                "account": str(self.scope.account_id),
                "owner": self.scope.owner_subject,
                "client": namespace,
                "grant": str(delegation_id),
                "peer_type": peer.peer_type,
                "provider_id": peer.provider_id,
                "expires_at": data["expires_at"],
            }
            if data != expected or expires.tzinfo is None:
                raise ValueError
        except (
            InvalidToken,
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
            AttributeError,
            CommandRuntimeError,
        ):
            raise CommandRuntimeError("peer_reference_invalid") from None
        if now >= expires:
            raise CommandRuntimeError("peer_reference_expired")
        return peer

    async def prepare(
        self,
        *,
        principal: Principal,
        grant_ref: str,
        peer_ref: str,
        request_key: str,
        text: str,
    ) -> dict[str, Any]:
        namespace = self._principal(principal, "gate:personal:send_text")
        delegation_id = _reference_id(grant_ref, "grant")
        if not isinstance(request_key, str):
            raise CommandRuntimeError("request_key_invalid")
        require_request_key(request_key)
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise CommandRuntimeError("command_text_invalid")
        now = self._now()
        peer = self._peer(peer_ref, namespace=namespace, delegation_id=delegation_id, now=now)
        async with self.store.sessions.begin() as session:
            delegation = await self.grants.require(
                session,
                principal=principal,
                delegation_id=delegation_id,
                capability="send_text",
                peer=peer,
                now=now,
            )
        return _result(
            await self.store.prepare(
                principal=principal,
                delegation=delegation,
                capability="send_text",
                peer=peer,
                request_key=request_key,
                payload={"text": text},
                now=now,
            )
        )

    async def execute(self, *, principal: Principal, operation_ref: str) -> dict[str, Any]:
        self._principal(principal, "gate:personal:send_text")
        return _result(
            await self.store.execute(
                _reference_id(operation_ref, "operation"), principal=principal, now=self._now()
            )
        )

    async def status(self, *, principal: Principal, operation_ref: str) -> dict[str, Any]:
        self._principal(principal, "gate:personal:jobs:read")
        return _result(
            await self.store.status(_reference_id(operation_ref, "operation"), principal=principal)
        )

    async def result(self, *, principal: Principal, operation_ref: str) -> dict[str, Any]:
        """Receipts remain readable after grant expiry/revocation by the same client."""
        return await self.status(principal=principal, operation_ref=operation_ref)

    async def cancel(self, *, principal: Principal, operation_ref: str) -> dict[str, Any]:
        self._principal(principal, "gate:personal:jobs:cancel")
        return _result(
            await self.store.cancel(
                _reference_id(operation_ref, "operation"), principal=principal, now=self._now()
            )
        )


def _reference_id(value: str, kind: str) -> UUID:
    if not isinstance(value, str) or not re.fullmatch(kind + r"_[0-9a-f]{32}", value):
        raise CommandRuntimeError(kind + "_reference_invalid")
    return UUID(hex=value[len(kind) + 1 :])


def _result(status: OperationStatus) -> dict[str, Any]:
    return {
        "operation_ref": "operation_" + status.operation_id.hex,
        "state": status.state,
        "receipt_ref": status.receipt_ref,
        "error_code": status.error_code,
        "replay": status.replay,
        "sent": False,
        "runtime_connected": False,
    }
