"""Telegram projection for connector-neutral shadow work ownership."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any, Protocol, cast

from app.application.work_ownership import (
    BusinessScope,
    DirectionEvidence,
    OwnershipSignal,
    ShadowWorkOwnershipEvaluation,
    WorkOwnershipFacts,
    evaluate_shadow_work_ownership,
)
from app.core.telegram_connectors import TelegramConnectorRole

JsonObject = dict[str, Any]
OKO_ENROLLMENT_PHRASE = "Око возрождения"
ALPHA_GROUP_COMMANDS = frozenset({"/alpha", "/ask"})


class TelegramIngressDecisionView(Protocol):
    """Minimal classifier view required by ownership shadowing."""

    @property
    def reason(self) -> str: ...

    @property
    def event_type(self) -> str | None: ...

    @property
    def command(self) -> str | None: ...


class TelegramIngressRoutingView(Protocol):
    """Minimal redacted routing view required by ownership shadowing."""

    @property
    def account_alias(self) -> str | None: ...

    @property
    def account_resolution(self) -> str: ...


def evaluate_telegram_work_ownership_shadow(
    update: JsonObject,
    *,
    decision: TelegramIngressDecisionView,
    routing: TelegramIngressRoutingView,
    connector_role: TelegramConnectorRole,
    bot_username: str,
    direction_evidence: DirectionEvidence = DirectionEvidence.UNKNOWN,
) -> ShadowWorkOwnershipEvaluation:
    """Project a Telegram update into a non-authoritative ownership result."""

    message, message_kind = _message(update)
    business_scope = _business_scope(
        account_alias=routing.account_alias,
        message_kind=message_kind,
    )
    unsupported_update = (
        decision.reason == "unsupported_update_shape" or decision.event_type is None
    )
    control_traffic = decision.command is not None or decision.event_type in {
        "business_connection",
        "callback_query",
    }
    routing_resolved = _routing_resolved(
        routing.account_resolution,
        connector_role=connector_role,
        message_kind=message_kind,
        message=message,
    )
    signal = _ownership_signal(
        message,
        connector_role=connector_role,
        message_kind=message_kind,
        bot_username=bot_username,
    )
    return evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=signal,
            business_scope=business_scope,
            direction_evidence=direction_evidence,
            control_traffic=control_traffic,
            unsupported_update=unsupported_update,
            routing_resolved=routing_resolved,
        )
    )


def normalize_oko_enrollment_text(value: str) -> str:
    """Apply the exact versioned normalization used only for Oko candidate detection."""

    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _message(update: Mapping[str, Any]) -> tuple[Mapping[str, Any], str | None]:
    business_message = update.get("business_message")
    message = update.get("message")
    has_business_message = isinstance(business_message, dict)
    has_message = isinstance(message, dict)
    if has_business_message and has_message:
        return {}, "ambiguous"
    if has_business_message:
        return cast(JsonObject, business_message), "business_message"
    if has_message:
        return cast(JsonObject, message), "message"
    return {}, None


def _business_scope(*, account_alias: str | None, message_kind: str | None) -> BusinessScope:
    if message_kind != "business_message":
        return BusinessScope.NOT_APPLICABLE
    if account_alias == "telegram_mark_ru":
        return BusinessScope.RU
    if account_alias == "telegram_mark_by":
        return BusinessScope.BY
    return BusinessScope.UNKNOWN


def _routing_resolved(
    account_resolution: str,
    *,
    connector_role: TelegramConnectorRole,
    message_kind: str | None,
    message: Mapping[str, Any],
) -> bool:
    if connector_role is TelegramConnectorRole.UNKNOWN or message_kind == "ambiguous":
        return False
    if account_resolution == "database_lookup_required":
        return False
    if message_kind == "business_message":
        return (
            connector_role is TelegramConnectorRole.BUSINESS
            and account_resolution in {"configured_connection", "database_snapshot"}
            and _chat_type(message) == "private"
        )
    if message_kind == "message":
        if (
            connector_role is TelegramConnectorRole.BUSINESS
            and message.get("business_connection_id") is not None
        ):
            return (
                account_resolution in {"configured_connection", "database_snapshot"}
                and _chat_type(message) == "private"
            )
        return _chat_type(message) in {"private", "group", "supergroup", "channel"}
    return True


def _ownership_signal(
    message: Mapping[str, Any],
    *,
    connector_role: TelegramConnectorRole,
    message_kind: str | None,
    bot_username: str,
) -> OwnershipSignal:
    text = message.get("text")
    text_value = text if isinstance(text, str) else ""
    chat_type = _chat_type(message)
    if (
        connector_role is TelegramConnectorRole.BUSINESS
        and message_kind == "business_message"
        and chat_type == "private"
        and _has_human_sender_shape(message)
        and normalize_oko_enrollment_text(text_value)
        == normalize_oko_enrollment_text(OKO_ENROLLMENT_PHRASE)
    ):
        return OwnershipSignal.OKO_EXACT_ENROLLMENT
    if (
        connector_role is TelegramConnectorRole.ALPHA_GROUP
        and message_kind == "message"
        and chat_type in {"group", "supergroup"}
        and _has_human_sender_shape(message)
        and _is_group_bot_invocation(
            message,
            text=text_value,
            bot_username=bot_username,
        )
    ):
        return OwnershipSignal.ALPHA_GROUP_INVOCATION
    return OwnershipSignal.NONE


def _chat_type(message: Mapping[str, Any]) -> str | None:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    value = chat.get("type")
    return value if isinstance(value, str) else None


def _is_group_bot_invocation(
    message: Mapping[str, Any],
    *,
    text: str,
    bot_username: str,
) -> bool:
    username = bot_username.strip().lstrip("@").casefold()
    if not username:
        return False
    if _is_reply_to_bot(message, username=username):
        return True

    first_token = text.strip().split(maxsplit=1)[0].casefold() if text.strip() else ""
    if first_token.startswith("/"):
        command, separator, addressed_username = first_token.partition("@")
        if command not in ALPHA_GROUP_COMMANDS:
            return False
        return not separator or addressed_username == username
    return f"@{username}" in text.casefold().split()


def _has_human_sender_shape(message: Mapping[str, Any]) -> bool:
    sender = message.get("from")
    return (
        isinstance(sender, dict)
        and sender.get("id") is not None
        and sender.get("is_bot") is False
        and not isinstance(message.get("sender_chat"), dict)
    )


def _is_reply_to_bot(message: Mapping[str, Any], *, username: str) -> bool:
    reply = message.get("reply_to_message")
    if not isinstance(reply, dict):
        return False
    sender = reply.get("from")
    if not isinstance(sender, dict) or sender.get("is_bot") is not True:
        return False
    reply_username = sender.get("username")
    return isinstance(reply_username, str) and reply_username.casefold() == username
