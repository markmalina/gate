"""Fail-closed orchestration for the single-group Alpha canary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.agent_kernel import AgentResultKind
from app.application.alpha_delivery import (
    AlphaDeliveryResult,
    DeliverAlphaGrantCommand,
)
from app.application.alpha_shadow_worker import (
    AlphaProposalDeliveryMode,
    AlphaShadowDisposition,
    AlphaShadowWorkerResult,
    ProcessAlphaShadowMessageCommand,
)
from app.application.canonical_messaging import DialogKind

JsonObject = dict[str, Any]
ALPHA_DISCLOSURE_PREFIX = (
    "AlphaViaBot — цифровой помощник. Ответ может содержать ошибки.\n\n"
)


class AlphaCanaryDisposition(StrEnum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    DISCOVERED = "discovered"
    REVOKED = "revoked"
    NOT_INVOKED = "not_invoked"
    PROPOSED = "proposed"
    SENT = "sent"
    DELIVERY_TERMINAL = "delivery_terminal"


class AlphaMembershipAction(StrEnum):
    PRESENT = "present"
    REMOVED = "removed"


class AlphaCanaryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "alpha_canary_error"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ProcessAlphaCanaryUpdateCommand:
    workspace_key: str
    update: JsonObject = field(repr=False)
    bot_username: str = field(repr=False)
    now: datetime
    canonical_message_id: str | None = field(default=None, repr=False)
    connector_enabled: bool = False
    shadow_worker_enabled: bool = False
    canary_enabled: bool = False
    delivery_enabled: bool = False
    privacy_mode_confirmed: bool = False
    credentials_ready: bool = False

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReconcileAlphaMembershipCommand:
    workspace_key: str
    connector_key: str
    provider_dialog_id: str = field(repr=False)
    dialog_kind: DialogKind
    dialog_label: str
    action: AlphaMembershipAction
    now: datetime


@dataclass(frozen=True, slots=True)
class IssueAlphaDeliveryGrantCommand:
    workspace_key: str
    turn_key: str
    trigger_message_id: str = field(repr=False)
    provider_dialog_id: str = field(repr=False)
    disclosure_prefix: str = field(repr=False)
    now: datetime


@dataclass(frozen=True, slots=True)
class AlphaMembershipResult:
    action: AlphaMembershipAction
    installation_key: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class AlphaGrantResult:
    grant_key: str = field(repr=False)
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class AlphaCanaryResult:
    disposition: AlphaCanaryDisposition
    code: str
    shadow: AlphaShadowWorkerResult | None = None
    delivery: AlphaDeliveryResult | None = None


class AlphaCanaryStore(Protocol):
    async def reconcile_membership(
        self, command: ReconcileAlphaMembershipCommand
    ) -> AlphaMembershipResult: ...

    async def issue_delivery_grant(
        self, command: IssueAlphaDeliveryGrantCommand
    ) -> AlphaGrantResult: ...

    async def is_active_group(
        self,
        *,
        workspace_key: str,
        connector_key: str,
        provider_dialog_id: str,
    ) -> bool: ...


class AlphaCanaryShadowPort(Protocol):
    async def execute(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowWorkerResult: ...


class AlphaCanaryDeliveryPort(Protocol):
    async def execute(self, command: DeliverAlphaGrantCommand) -> AlphaDeliveryResult: ...


class ProcessAlphaCanaryUpdateService:
    """Connect durable ingress to shadow and delivery only behind every canary gate."""

    def __init__(
        self,
        *,
        store: AlphaCanaryStore,
        shadow: AlphaCanaryShadowPort,
        delivery: AlphaCanaryDeliveryPort,
        connector_key: str = "telegram_alpha_group",
    ) -> None:
        self._store = store
        self._shadow = shadow
        self._delivery = delivery
        self._connector_key = connector_key

    async def execute(
        self, command: ProcessAlphaCanaryUpdateCommand
    ) -> AlphaCanaryResult:
        readiness_code = _readiness_code(command)
        if readiness_code is not None:
            disposition = (
                AlphaCanaryDisposition.DISABLED
                if readiness_code.endswith("disabled")
                else AlphaCanaryDisposition.BLOCKED
            )
            return AlphaCanaryResult(disposition, readiness_code)

        kind, payload = _closed_update(command.update)
        dialog_id, dialog_kind, dialog_label = _group(payload)
        if kind == "message" and not await self._store.is_active_group(
            workspace_key=command.workspace_key,
            connector_key=self._connector_key,
            provider_dialog_id=dialog_id,
        ):
            return AlphaCanaryResult(
                AlphaCanaryDisposition.BLOCKED,
                "alpha_canary_group_not_active",
            )

        if kind == "my_chat_member":
            action = _membership_action(payload, bot_username=command.bot_username)
            membership = await self._store.reconcile_membership(
                ReconcileAlphaMembershipCommand(
                    workspace_key=command.workspace_key,
                    connector_key=self._connector_key,
                    provider_dialog_id=dialog_id,
                    dialog_kind=dialog_kind,
                    dialog_label=dialog_label,
                    action=action,
                    now=command.now,
                )
            )
            disposition = (
                AlphaCanaryDisposition.DISCOVERED
                if membership.action is AlphaMembershipAction.PRESENT
                else AlphaCanaryDisposition.REVOKED
            )
            return AlphaCanaryResult(disposition, f"alpha_membership_{action.value}")

        if not _has_nonblank_text(payload):
            return AlphaCanaryResult(
                AlphaCanaryDisposition.NOT_INVOKED,
                "alpha_canary_non_text_ignored",
            )

        if command.canonical_message_id is None:
            raise AlphaCanaryError("alpha_canary_canonical_message_required")
        shadow = await self._shadow.execute(
            ProcessAlphaShadowMessageCommand(
                workspace_key=command.workspace_key,
                trigger_message_id=command.canonical_message_id,
                bot_username=command.bot_username,
                now=command.now,
            )
        )
        if shadow.disposition is AlphaShadowDisposition.NOT_INVOKED:
            return AlphaCanaryResult(
                AlphaCanaryDisposition.NOT_INVOKED,
                shadow.code,
                shadow=shadow,
            )
        if shadow.disposition not in {
            AlphaShadowDisposition.PROPOSED,
            AlphaShadowDisposition.REPLAYED,
        } or shadow.result_kind is not AgentResultKind.REPLY:
            return AlphaCanaryResult(
                AlphaCanaryDisposition.BLOCKED,
                shadow.code,
                shadow=shadow,
            )
        if shadow.delivery_mode is AlphaProposalDeliveryMode.SHADOW_ONLY:
            return AlphaCanaryResult(
                AlphaCanaryDisposition.PROPOSED,
                "alpha_model_shadow_delivery_blocked",
                shadow=shadow,
            )
        if not command.delivery_enabled:
            return AlphaCanaryResult(
                AlphaCanaryDisposition.PROPOSED,
                "alpha_canary_delivery_disabled",
                shadow=shadow,
            )
        if shadow.turn_key is None:
            raise AlphaCanaryError("alpha_canary_turn_identity_missing")

        grant = await self._store.issue_delivery_grant(
            IssueAlphaDeliveryGrantCommand(
                workspace_key=command.workspace_key,
                turn_key=shadow.turn_key,
                trigger_message_id=command.canonical_message_id,
                provider_dialog_id=dialog_id,
                disclosure_prefix=ALPHA_DISCLOSURE_PREFIX,
                now=command.now,
            )
        )
        delivery = await self._delivery.execute(
            DeliverAlphaGrantCommand(
                workspace_key=command.workspace_key,
                grant_key=grant.grant_key,
                now=command.now,
            )
        )
        return AlphaCanaryResult(
            AlphaCanaryDisposition.SENT
            if delivery.sent
            else AlphaCanaryDisposition.DELIVERY_TERMINAL,
            "alpha_canary_sent" if delivery.sent else (delivery.error_code or delivery.state.value),
            shadow=shadow,
            delivery=delivery,
        )


def _readiness_code(command: ProcessAlphaCanaryUpdateCommand) -> str | None:
    if not command.connector_enabled:
        return "alpha_connector_disabled"
    if not command.shadow_worker_enabled:
        return "alpha_shadow_worker_disabled"
    if not command.canary_enabled:
        return "alpha_canary_disabled"
    if not command.privacy_mode_confirmed:
        return "alpha_privacy_mode_not_confirmed"
    if not command.credentials_ready:
        return "alpha_canary_credentials_incomplete"
    if not command.bot_username:
        return "alpha_canary_scope_incomplete"
    return None


def _closed_update(update: JsonObject) -> tuple[str, JsonObject]:
    kinds = [key for key in ("message", "my_chat_member") if isinstance(update.get(key), dict)]
    provider_kinds = [key for key in update if key != "update_id"]
    if len(kinds) != 1 or provider_kinds != kinds:
        raise AlphaCanaryError("alpha_canary_update_shape_invalid")
    payload = update[kinds[0]]
    if not isinstance(payload, dict):
        raise AlphaCanaryError("alpha_canary_update_shape_invalid")
    return kinds[0], payload


def _group(payload: JsonObject) -> tuple[str, DialogKind, str]:
    chat = payload.get("chat")
    if not isinstance(chat, dict) or chat.get("type") not in {"group", "supergroup"}:
        raise AlphaCanaryError("alpha_canary_dialog_not_group")
    chat_id = chat.get("id")
    if not isinstance(chat_id, (str, int)) or isinstance(chat_id, bool):
        raise AlphaCanaryError("alpha_canary_dialog_identity_invalid")
    label = chat.get("title")
    label_value = (
        label.strip()[:255]
        if isinstance(label, str) and label.strip()
        else "Alpha canary group"
    )
    return str(chat_id), DialogKind(str(chat["type"])), label_value


def _membership_action(payload: JsonObject, *, bot_username: str) -> AlphaMembershipAction:
    member = payload.get("new_chat_member")
    if not isinstance(member, dict):
        raise AlphaCanaryError("alpha_membership_snapshot_missing")
    user = member.get("user")
    if not isinstance(user, dict) or user.get("is_bot") is not True:
        raise AlphaCanaryError("alpha_membership_bot_identity_invalid")
    username = user.get("username")
    expected = bot_username.strip().lstrip("@").casefold()
    if not isinstance(username, str) or username.casefold() != expected:
        raise AlphaCanaryError("alpha_membership_bot_identity_invalid")
    status = member.get("status")
    if status in {"member", "administrator", "creator"}:
        return AlphaMembershipAction.PRESENT
    if status in {"left", "kicked"}:
        return AlphaMembershipAction.REMOVED
    raise AlphaCanaryError("alpha_membership_status_invalid")


def _has_nonblank_text(payload: JsonObject) -> bool:
    text = payload.get("text")
    return isinstance(text, str) and bool(text.strip())


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded token")
