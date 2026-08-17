"""Pure Telegram projection into connector-neutral canonical messaging facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    CanonicalMessageEnvelope,
    ConnectorKind,
    DialogIdentity,
    DialogKind,
    WorkOwnershipDecision,
    WorkRoute,
)
from app.application.telegram_message_content import extract_telegram_message_text
from app.application.work_ownership import (
    DirectionEvidence,
    ShadowWorkOwnershipEvaluation,
    WorkOwnershipState,
)

TELEGRAM_SAFE_PROVIDER_ID_MAX = (1 << 52) - 1


class TelegramCanonicalProjectionError(ValueError):
    """A message-shaped update cannot be projected without guessing identity."""


@dataclass(frozen=True, slots=True)
class TelegramCanonicalProjection:
    """One complete canonical persistence command without raw text in repr."""

    dialog: DialogIdentity
    message: CanonicalMessageEnvelope
    ownership: WorkOwnershipDecision


def project_telegram_canonical_message(
    update: Mapping[str, Any],
    *,
    workspace_id: str,
    connector_key: str,
    connector_kind: ConnectorKind,
    source_receipt_id: str,
    received_at: datetime,
    direction_evidence: DirectionEvidence,
    reply_task_intent: str,
    ownership: ShadowWorkOwnershipEvaluation,
    control_traffic: bool = False,
) -> TelegramCanonicalProjection | None:
    """Project a supported message or return no canonical fact for control traffic."""

    if control_traffic:
        return None
    selected = _single_message(update)
    if selected is None:
        return None
    chat = selected.get("chat")
    if not isinstance(chat, dict):
        raise TelegramCanonicalProjectionError("canonical_chat_missing")
    provider_dialog_id = _provider_id(chat.get("id"))
    if provider_dialog_id is None:
        raise TelegramCanonicalProjectionError("canonical_dialog_id_invalid")
    provider_message_id = _provider_id(selected.get("message_id"), positive=True)
    if provider_message_id is None:
        raise TelegramCanonicalProjectionError("canonical_message_id_invalid")
    chat_type = chat.get("type")
    if not isinstance(chat_type, str):
        raise TelegramCanonicalProjectionError("canonical_dialog_kind_invalid")
    try:
        dialog_kind = DialogKind(chat_type)
    except (TypeError, ValueError):
        raise TelegramCanonicalProjectionError("canonical_dialog_kind_invalid") from None

    sender_id = _sender_id(selected)
    direction = _canonical_direction(direction_evidence)
    text_content = extract_telegram_message_text(selected)
    text_present = text_content is not None
    input_origin = _input_origin(selected)
    occurred_at = _occurred_at(selected.get("date"), fallback=received_at)
    dialog = DialogIdentity(
        workspace_id=workspace_id,
        connector_key=connector_key,
        connector_kind=connector_kind,
        provider_dialog_id=provider_dialog_id,
        dialog_kind=dialog_kind,
    )
    message = CanonicalMessageEnvelope(
        workspace_id=workspace_id,
        connector_key=connector_key,
        source_receipt_id=source_receipt_id,
        provider_dialog_id=provider_dialog_id,
        provider_message_id=provider_message_id,
        provider_sender_id=sender_id,
        direction=direction,
        occurred_at=occurred_at,
        text_present=text_present,
        text_content=text_content,
        input_origin=input_origin,
    )
    decision = WorkOwnershipDecision(
        state=ownership.proposed_state,
        route=_route(
            ownership=ownership,
            direction_evidence=direction_evidence,
            reply_task_intent=reply_task_intent,
            text_present=text_present,
        ),
        policy_version=ownership.policy_version,
        candidate_agent=ownership.candidate_agent,
        reason_code=ownership.reason.value,
    )
    return TelegramCanonicalProjection(
        dialog=dialog,
        message=message,
        ownership=decision,
    )


def _single_message(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    business_message = update.get("business_message")
    message = update.get("message")
    has_business = isinstance(business_message, dict)
    has_message = isinstance(message, dict)
    if has_business == has_message:
        return None
    return cast(Mapping[str, Any], business_message if has_business else message)



def _input_origin(message: Mapping[str, Any]) -> str:
    value = message.get("gate_input_origin")
    if value is None:
        return "telegram_text"
    if value == "telegram_voice_transcription":
        return "telegram_voice_transcription"
    raise TelegramCanonicalProjectionError("canonical_input_origin_invalid")

def _sender_id(message: Mapping[str, Any]) -> str | None:
    sender_chat = message.get("sender_chat")
    if isinstance(sender_chat, dict):
        return _provider_id(sender_chat.get("id"))
    sender = message.get("from")
    if not isinstance(sender, dict):
        return None
    return _provider_id(sender.get("id"))


def _canonical_direction(evidence: DirectionEvidence) -> CanonicalMessageDirection:
    if evidence is DirectionEvidence.HUMAN_INBOUND:
        return CanonicalMessageDirection.INBOUND
    if evidence is DirectionEvidence.OWNER_OUTBOUND:
        return CanonicalMessageDirection.OUTBOUND
    if evidence is DirectionEvidence.BOT_OR_SERVICE:
        return CanonicalMessageDirection.BOT_OR_SERVICE
    return CanonicalMessageDirection.UNKNOWN


def _route(
    *,
    ownership: ShadowWorkOwnershipEvaluation,
    direction_evidence: DirectionEvidence,
    reply_task_intent: str,
    text_present: bool,
) -> WorkRoute:
    if ownership.proposed_state is WorkOwnershipState.QUARANTINED:
        return WorkRoute.QUARANTINED
    if ownership.proposed_state is WorkOwnershipState.IGNORED:
        return WorkRoute.IGNORED
    if reply_task_intent == "create_inbound_human_text":
        return WorkRoute.HUMAN_REPLY_TASK
    if ownership.candidate_agent is not None:
        return WorkRoute.HUMAN_REPLY_TASK
    if direction_evidence is DirectionEvidence.HUMAN_INBOUND and text_present:
        return WorkRoute.HUMAN_REPLY_TASK
    return WorkRoute.IGNORED


def _occurred_at(value: object, *, fallback: datetime) -> datetime:
    if fallback.tzinfo is None:
        raise TelegramCanonicalProjectionError("canonical_received_at_invalid")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, OverflowError, ValueError):
            pass
    return fallback


def _provider_id(value: object, *, positive: bool = False) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value == 0 or abs(value) > TELEGRAM_SAFE_PROVIDER_ID_MAX:
        return None
    if positive and value < 0:
        return None
    return str(value)
