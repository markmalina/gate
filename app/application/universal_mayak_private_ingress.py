"""Default-off private Telegram ingress for the universal Mayak demo.

This boundary deliberately knows neither Telegram credentials nor database
models. Provider identities are resolved to an approved opaque binding before
the application service sees a message. The model never receives provider
identifiers and cannot choose a recipient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.universal_mayak_demo import (
    DemoInvocation,
    DemoScope,
    ProcessUniversalMayakCommand,
    UniversalMayakDemoResult,
)

JsonObject = dict[str, Any]
TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807
TELEGRAM_MESSAGE_ID_MAX = 9_223_372_036_854_775_807


class UniversalMayakPrivateIngressError(RuntimeError):
    """A stable, payload-free refusal at the private-dialog boundary."""

    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "mayak_private_ingress_error"
        super().__init__(self.code)


class PrivateIngressDisposition(StrEnum):
    DISABLED = "disabled"
    NOT_APPLICABLE = "not_applicable"
    BLOCKED = "blocked"
    PROCESSED = "processed"


@dataclass(frozen=True, slots=True)
class ApprovedPrivateBinding:
    """An explicit opaque installation binding resolved outside the model."""

    scope: DemoScope
    actor_scope: str
    active: bool

    def __post_init__(self) -> None:
        if (
            not self.actor_scope
            or self.actor_scope != self.actor_scope.strip()
            or len(self.actor_scope) > 64
        ):
            raise ValueError("actor_scope must be a bounded token")


@dataclass(frozen=True, slots=True)
class ProcessUniversalMayakPrivateUpdateCommand:
    update: JsonObject = field(repr=False)
    runtime_enabled: bool
    connector_enabled: bool
    credentials_ready: bool
    now: datetime


@dataclass(frozen=True, slots=True)
class UniversalMayakPrivateIngressResult:
    disposition: PrivateIngressDisposition
    code: str
    result: UniversalMayakDemoResult | None = None


class ApprovedPrivateBindingResolver(Protocol):
    """Resolve one provider pair to a pre-approved opaque installation."""

    async def resolve(
        self,
        *,
        provider_dialog_id: str,
        provider_actor_id: str,
    ) -> ApprovedPrivateBinding | None: ...


class UniversalMayakCommandPort(Protocol):
    """The already bounded universal Mayak application service."""

    async def execute(
        self, command: ProcessUniversalMayakCommand
    ) -> UniversalMayakDemoResult: ...


class ProcessUniversalMayakPrivateUpdateService:
    """Map one exact private text update into the universal Mayak service."""

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
        command: ProcessUniversalMayakPrivateUpdateCommand,
    ) -> UniversalMayakPrivateIngressResult:
        readiness = _readiness_code(command)
        if readiness is not None:
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.DISABLED,
                readiness,
            )

        normalized = _private_text(command.update)
        if normalized is None:
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.NOT_APPLICABLE,
                "mayak_private_non_text_ignored",
            )
        update_id, message_id, dialog_id, actor_id, text = normalized
        binding = await self._bindings.resolve(
            provider_dialog_id=dialog_id,
            provider_actor_id=actor_id,
        )
        if binding is None or not binding.active:
            return UniversalMayakPrivateIngressResult(
                PrivateIngressDisposition.BLOCKED,
                "mayak_private_binding_not_active",
            )

        result = await self._mayak.execute(
            ProcessUniversalMayakCommand(
                scope=binding.scope,
                event_ref=f"telegram_update_{update_id}",
                idempotency_key=f"telegram_message_{dialog_id}_{message_id}",
                invocation=_invocation(text),
                text=text,
                actor_scope=binding.actor_scope,
                now=command.now,
            )
        )
        return UniversalMayakPrivateIngressResult(
            PrivateIngressDisposition.PROCESSED,
            "mayak_private_processed",
            result,
        )


def _readiness_code(
    command: ProcessUniversalMayakPrivateUpdateCommand,
) -> str | None:
    if not command.connector_enabled:
        return "mayak_private_connector_disabled"
    if not command.runtime_enabled:
        return "mayak_private_runtime_disabled"
    if not command.credentials_ready:
        return "mayak_private_credentials_incomplete"
    return None


def _private_text(
    update: JsonObject,
) -> tuple[int, int, str, str, str] | None:
    update_id = update.get("update_id")
    if (
        type(update_id) is not int
        or update_id < 0
        or update_id > TELEGRAM_UPDATE_ID_MAX
    ):
        raise UniversalMayakPrivateIngressError(
            "mayak_private_update_identity_invalid"
        )
    if [key for key in update if key != "update_id"] != ["message"]:
        raise UniversalMayakPrivateIngressError("mayak_private_update_shape_invalid")
    message = update.get("message")
    if not isinstance(message, dict):
        raise UniversalMayakPrivateIngressError("mayak_private_update_shape_invalid")
    message_id = message.get("message_id")
    if (
        type(message_id) is not int
        or message_id < 0
        or message_id > TELEGRAM_MESSAGE_ID_MAX
    ):
        raise UniversalMayakPrivateIngressError(
            "mayak_private_message_identity_invalid"
        )
    chat = message.get("chat")
    actor = message.get("from")
    if (
        not isinstance(chat, dict)
        or chat.get("type") != "private"
        or not isinstance(actor, dict)
        or actor.get("is_bot") is True
    ):
        raise UniversalMayakPrivateIngressError("mayak_private_scope_invalid")
    dialog_id = _provider_id(chat.get("id"), "dialog")
    actor_id = _provider_id(actor.get("id"), "actor")
    text = message.get("text")
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
        raise UniversalMayakPrivateIngressError("mayak_private_text_invalid")
    return update_id, message_id, dialog_id, actor_id, text.strip()


def _provider_id(value: object, kind: str) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise UniversalMayakPrivateIngressError(
            f"mayak_private_{kind}_identity_invalid"
        )
    normalized = str(value).strip()
    if not normalized or len(normalized) > 128:
        raise UniversalMayakPrivateIngressError(
            f"mayak_private_{kind}_identity_invalid"
        )
    return normalized


def _invocation(text: str) -> DemoInvocation:
    if text == "/start":
        return DemoInvocation.START
    if text == "/pause":
        return DemoInvocation.PAUSE
    if text == "/human":
        return DemoInvocation.HUMAN_TAKEOVER
    return DemoInvocation.DIRECT
