"""Owner-only Universal Mayak admission from canonical Telegram Business text.

The durable Telegram worker has already authenticated, classified and persisted
the Business update before this boundary runs. Provider identifiers are used
only to resolve one exact approved binding; the Mayak command receives opaque
Gate UUIDs and cannot choose an account, connection, dialog or recipient.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.universal_mayak_demo import (
    DemoInvocation,
    DemoScope,
    ProcessUniversalMayakCommand,
    UniversalMayakDemoResult,
)


class UniversalMayakPrivateIngressError(RuntimeError):
    """A stable, payload-free refusal at the Business admission boundary."""

    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "mayak_private_ingress_error"
        super().__init__(self.code)


class PrivateIngressDisposition(StrEnum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    PROCESSED = "processed"


@dataclass(frozen=True, slots=True)
class ApprovedPrivateBinding:
    """One exact database-authoritative owner-only Business installation."""

    scope: DemoScope
    actor_scope: str
    active: bool
    account_id: str = field(repr=False)
    project_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    provider_dialog_id: str = field(repr=False)
    provider_actor_id: str = field(repr=False)

    def __post_init__(self) -> None:
        _uuid(self.account_id, "account_id")
        _uuid(self.project_id, "project_id")
        _token(self.actor_scope, "actor_scope", 64)
        _token(self.business_connection_id, "business_connection_id", 255)
        _token(self.provider_dialog_id, "provider_dialog_id", 255)
        _token(self.provider_actor_id, "provider_actor_id", 255)


@dataclass(frozen=True, slots=True)
class ProcessUniversalMayakPrivateMessageCommand:
    """Canonical Business message plus minimized exact binding evidence."""

    account_id: str = field(repr=False)
    project_id: str = field(repr=False)
    canonical_message_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    provider_dialog_id: str = field(repr=False)
    provider_actor_id: str = field(repr=False)
    direction: CanonicalMessageDirection
    current_route: WorkRoute
    text: str = field(repr=False)
    runtime_enabled: bool
    connector_enabled: bool
    credentials_ready: bool
    now: datetime

    def __post_init__(self) -> None:
        _uuid(self.account_id, "account_id")
        _uuid(self.project_id, "project_id")
        _uuid(self.canonical_message_id, "canonical_message_id")
        _token(self.business_connection_id, "business_connection_id", 255)
        _token(self.provider_dialog_id, "provider_dialog_id", 255)
        _token(self.provider_actor_id, "provider_actor_id", 255)
        _text(self.text, "text", 4_000)
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class UniversalMayakPrivateIngressResult:
    disposition: PrivateIngressDisposition
    code: str
    suppress_human_reply_task: bool = False
    result: UniversalMayakDemoResult | None = None

    def __post_init__(self) -> None:
        if self.suppress_human_reply_task != (
            self.disposition is PrivateIngressDisposition.PROCESSED
        ):
            raise ValueError("only a processed Mayak route suppresses human work")


class ApprovedPrivateBindingResolver(Protocol):
    """Resolve one exact receiving-account-relative binding from SQL."""

    async def resolve(
        self,
        *,
        account_id: str,
        project_id: str,
        canonical_message_id: str,
        business_connection_id: str,
        provider_dialog_id: str,
        provider_actor_id: str,
    ) -> ApprovedPrivateBinding | None: ...


class UniversalMayakCommandPort(Protocol):
    """The already bounded universal Mayak application service."""

    async def execute(
        self, command: ProcessUniversalMayakCommand
    ) -> UniversalMayakDemoResult: ...


class ProcessUniversalMayakPrivateMessageService:
    """Map one canonical inbound Business text to one exact Mayak command."""

    def __init__(
        self,
        *,
        bindings: ApprovedPrivateBindingResolver,
        mayak: UniversalMayakCommandPort,
    ) -> None:
        self._bindings = bindings
        self._mayak = mayak

    async def execute(
        self,
        command: ProcessUniversalMayakPrivateMessageCommand,
    ) -> UniversalMayakPrivateIngressResult:
        readiness = _readiness_code(command)
        if readiness is not None:
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.DISABLED,
                readiness,
            )
        if (
            command.direction is not CanonicalMessageDirection.INBOUND
            or command.current_route is not WorkRoute.HUMAN_REPLY_TASK
        ):
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.BLOCKED,
                "mayak_private_route_not_eligible",
            )

        binding = await self._bindings.resolve(
            account_id=command.account_id,
            project_id=command.project_id,
            canonical_message_id=command.canonical_message_id,
            business_connection_id=command.business_connection_id,
            provider_dialog_id=command.provider_dialog_id,
            provider_actor_id=command.provider_actor_id,
        )
        if binding is None or not binding.active:
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.BLOCKED,
                "mayak_private_binding_not_active",
            )
        _require_exact_binding(binding, command)

        result = await self._mayak.execute(
            ProcessUniversalMayakCommand(
                scope=binding.scope,
                event_ref=f"canonical_message_{command.canonical_message_id}",
                idempotency_key=f"mayak_message_{command.canonical_message_id}",
                invocation=_invocation(command.text),
                text=command.text,
                actor_scope=binding.actor_scope,
                now=command.now,
            )
        )
        return UniversalMayakPrivateIngressResult(
            PrivateIngressDisposition.PROCESSED,
            "mayak_private_processed",
            suppress_human_reply_task=True,
            result=result,
        )


def _readiness_code(
    command: ProcessUniversalMayakPrivateMessageCommand,
) -> str | None:
    if not command.connector_enabled:
        return "mayak_private_connector_disabled"
    if not command.runtime_enabled:
        return "mayak_private_runtime_disabled"
    if not command.credentials_ready:
        return "mayak_private_credentials_incomplete"
    return None


def _require_exact_binding(
    binding: ApprovedPrivateBinding,
    command: ProcessUniversalMayakPrivateMessageCommand,
) -> None:
    expected = (
        command.account_id,
        command.project_id,
        command.business_connection_id,
        command.provider_dialog_id,
        command.provider_actor_id,
    )
    actual = (
        binding.account_id,
        binding.project_id,
        binding.business_connection_id,
        binding.provider_dialog_id,
        binding.provider_actor_id,
    )
    if actual != expected:
        raise UniversalMayakPrivateIngressError(
            "mayak_private_binding_scope_mismatch"
        )


def _invocation(text: str) -> DemoInvocation:
    if text == "/start":
        return DemoInvocation.START
    if text == "/pause":
        return DemoInvocation.PAUSE
    if text == "/human":
        return DemoInvocation.HUMAN_TAKEOVER
    return DemoInvocation.DIRECT


def _uuid(value: str, name: str) -> None:
    try:
        uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _token(value: str, name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded")


def _text(value: str, name: str, maximum: int) -> None:
    if not value or not value.strip() or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded")
