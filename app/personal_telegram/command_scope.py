"""Immutable RU-only command boundaries, independent of Business OAuth fallback."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.mcp_security import Principal
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID


class CommandRuntimeError(RuntimeError):
    """Only stable codes escape the worker; provider/secret text never does."""


@dataclass(frozen=True, slots=True)
class RuCommandScope:
    workspace: str
    account_id: UUID
    binding_id: UUID
    owner_subject: str

    def validate(self) -> None:
        if self.account_id != TELEGRAM_MARK_RU_ACCOUNT_ID:
            raise CommandRuntimeError("ru_account_required")
        if (
            not self.workspace.strip()
            or len(self.workspace) > 128
            or not self.owner_subject.strip()
            or len(self.owner_subject) > 256
        ):
            raise CommandRuntimeError("binding_scope_invalid")


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    peer_type: str
    provider_id: int

    def validate(self) -> None:
        if self.peer_type not in {"user", "chat", "channel", "self"}:
            raise CommandRuntimeError("peer_type_invalid")
        if type(self.provider_id) is not int or not 0 < self.provider_id < 2**63:
            raise CommandRuntimeError("peer_id_invalid")

    @property
    def key(self) -> str:
        self.validate()
        return f"{self.peer_type}:{self.provider_id}"


@dataclass(frozen=True, slots=True)
class CommandDelegation:
    delegation_id: UUID
    scope: RuCommandScope
    client_namespace: str
    capabilities: frozenset[str]
    peer_keys: frozenset[str]
    expires_at: datetime
    revoked: bool = False

    def admit(
        self, *, principal: Principal, capability: str, peer: PeerIdentity, now: datetime
    ) -> None:
        self.scope.validate()
        peer.validate()
        if now.tzinfo is None or self.expires_at.tzinfo is None:
            raise CommandRuntimeError("timezone_required")
        if self.revoked or now >= self.expires_at:
            raise CommandRuntimeError("delegation_inactive")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.client_namespace) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", capability
        ):
            raise CommandRuntimeError("delegation_scope_invalid")
        if (
            principal.auth_method != "oauth2"
            or principal.subject != self.scope.owner_subject
            or principal.access_scope.workspace_id != self.scope.workspace
            or self.scope.account_id not in principal.access_scope.account_ids
        ):
            raise CommandRuntimeError("ru_scope_denied")
        if (
            capability not in self.capabilities
            or f"gate:personal:{capability}" not in principal.scopes
            or peer.key not in self.peer_keys
        ):
            raise CommandRuntimeError("command_scope_denied")


def command_fingerprint(
    *,
    scope: RuCommandScope,
    namespace: str,
    capability: str,
    peer: PeerIdentity,
    payload: dict[str, Any],
    delegation_id: UUID,
) -> str:
    """Payload identity is NOT a key for suppressing independent commands."""
    scope.validate()
    peer.validate()
    content = json.dumps(
        {
            "binding": str(scope.binding_id),
            "account": str(scope.account_id),
            "workspace": scope.workspace,
            "owner": scope.owner_subject,
            "client": namespace,
            "capability": capability,
            "peer": peer.key,
            "delegation": str(delegation_id),
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(content.encode()).hexdigest()


def require_request_key(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise CommandRuntimeError("request_key_invalid")


def utc_now() -> datetime:
    return datetime.now(UTC)
