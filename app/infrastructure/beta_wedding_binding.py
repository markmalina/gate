"""Fail-closed owner-only binding for the Wedding in Minsk Beta Space."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.beta_space import BetaSpaceBindingModel, BetaSpaceEventModel
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import TelegramReceipt

from .beta_space import BETA_CONNECTOR_KEY, beta_space_production_ready

BETA_WEDDING_SPACE_REF = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"
BETA_WEDDING_TENANT_REF = "beta-user-mark-wedding-001"
BETA_WEDDING_INSTALLATION_REF = "installation-4de1df8a77f8468d8b30e8a41db876aa"
BETA_WEDDING_STORAGE_REF = "beta-group-wedding-minsk-001"
BETA_WEDDING_GROUP_TITLE = "Свадьба в Минске"
BETA_WEDDING_ACTIVATION_TEXT = "Бета, начинаем собирать свадьбу в Минске."


class BetaWeddingBindingError(RuntimeError):
    """Reject ambiguous or conflicting owner-only wedding binding."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BetaWeddingActivationCandidate:
    telegram_chat_id: str = field(repr=False)
    telegram_user_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BetaWeddingGroupCandidate:
    telegram_chat_id: str = field(repr=False)
    received_at: datetime


def extract_beta_wedding_group_candidate(
    *, payload: object, received_at: datetime
) -> BetaWeddingGroupCandidate | None:
    """Select the exact fresh wedding group from durable bot-membership evidence."""

    if received_at.tzinfo is None or not isinstance(payload, dict):
        return None
    membership = payload.get("my_chat_member")
    message = payload.get("message")
    if isinstance(membership, dict):
        chat = membership.get("chat")
        new_member = membership.get("new_chat_member")
        bot = new_member.get("user") if isinstance(new_member, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if (
            not isinstance(chat, dict)
            or chat.get("type") not in {"group", "supergroup"}
            or chat.get("title") != BETA_WEDDING_GROUP_TITLE
            or type(chat_id) is not int
            or not isinstance(new_member, dict)
            or new_member.get("status") not in {"member", "administrator"}
            or not isinstance(bot, dict)
            or bot.get("is_bot") is not True
        ):
            return None
    elif isinstance(message, dict):
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if (
            not isinstance(chat, dict)
            or chat.get("type") not in {"group", "supergroup"}
            or chat.get("title") != BETA_WEDDING_GROUP_TITLE
            or type(chat_id) is not int
        ):
            return None
    else:
        return None
    return BetaWeddingGroupCandidate(
        telegram_chat_id=str(chat_id),
        received_at=received_at,
    )


def extract_beta_wedding_creator(administrators: object) -> str | None:
    """Return the one exact non-bot Mark creator; reject every ambiguity."""

    if not isinstance(administrators, list):
        return None
    matches: list[int] = []
    for administrator in administrators:
        if (
            not isinstance(administrator, dict)
            or administrator.get("status") != "creator"
        ):
            continue
        actor = administrator.get("user")
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        first_name = actor.get("first_name") if isinstance(actor, dict) else None
        if (
            isinstance(actor, dict)
            and actor.get("is_bot") is False
            and isinstance(first_name, str)
            and first_name.strip().casefold().split(maxsplit=1)[0] == "марк"
            and type(actor_id) is int
            and actor_id > 0
        ):
            matches.append(actor_id)
    return str(matches[0]) if len(set(matches)) == 1 else None


async def find_beta_wedding_group_candidates(
    session: AsyncSession,
) -> list[BetaWeddingGroupCandidate]:
    """Return exactly one newest durable wedding membership candidate."""

    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(TelegramReceipt.bot_key == BETA_CONNECTOR_KEY)
            .order_by(TelegramReceipt.received_at, TelegramReceipt.id)
        )
    ).all()
    by_chat: dict[str, BetaWeddingGroupCandidate] = {}
    for receipt in receipts:
        candidate = extract_beta_wedding_group_candidate(
            payload=receipt.payload,
            received_at=receipt.received_at,
        )
        if candidate is not None:
            by_chat[candidate.telegram_chat_id] = candidate
    if not by_chat:
        raise BetaWeddingBindingError("beta_wedding_group_candidate_missing")
    if len(by_chat) != 1:
        raise BetaWeddingBindingError("beta_wedding_group_candidate_ambiguous")
    return sorted(
        by_chat.values(),
        key=lambda candidate: candidate.received_at,
        reverse=True,
    )


def extract_beta_wedding_activation_candidate(
    payload: object,
) -> BetaWeddingActivationCandidate | None:
    """Return one exact owner activation without exposing private identifiers."""

    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if (
        not isinstance(message, dict)
        or message.get("text") != BETA_WEDDING_ACTIVATION_TEXT
    ):
        return None
    chat = message.get("chat")
    actor = message.get("from")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    actor_id = actor.get("id") if isinstance(actor, dict) else None
    first_name = actor.get("first_name") if isinstance(actor, dict) else None
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or chat.get("title") != BETA_WEDDING_GROUP_TITLE
        or type(chat_id) is not int
        or not isinstance(actor, dict)
        or actor.get("is_bot") is not False
        or type(actor_id) is not int
        or actor_id <= 0
        or not isinstance(first_name, str)
        or not first_name.strip()
        or first_name.strip().casefold().split(maxsplit=1)[0] != "марк"
    ):
        return None
    return BetaWeddingActivationCandidate(
        telegram_chat_id=str(chat_id),
        telegram_user_id=str(actor_id),
    )


async def prepare_beta_wedding_binding(
    session: AsyncSession,
    *,
    workspace_key: str,
    now: datetime,
    candidate: BetaWeddingActivationCandidate,
) -> str:
    """Atomically create one already-consented owner-only binding."""

    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise BetaWeddingBindingError("beta_wedding_workspace_missing")
    connector = await session.scalar(
        select(TransportConnector).where(
            TransportConnector.workspace_id == workspace.id,
            TransportConnector.connector_key == BETA_CONNECTOR_KEY,
            TransportConnector.is_enabled.is_(True),
        )
    )
    if connector is None:
        raise BetaWeddingBindingError("beta_wedding_connector_missing")
    existing = await session.scalar(
        select(BetaSpaceBindingModel)
        .where(
            BetaSpaceBindingModel.workspace_id == workspace.id,
            BetaSpaceBindingModel.space_ref == BETA_WEDDING_SPACE_REF,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.state != "consented"
            or existing.telegram_chat_id != candidate.telegram_chat_id
            or existing.subject_telegram_user_id != candidate.telegram_user_id
        ):
            raise BetaWeddingBindingError("beta_wedding_binding_conflict")
        return "already_bound"
    collision = await session.scalar(
        select(BetaSpaceBindingModel.id).where(
            BetaSpaceBindingModel.connector_id == connector.id,
            BetaSpaceBindingModel.telegram_chat_id == candidate.telegram_chat_id,
        )
    )
    if collision is not None:
        raise BetaWeddingBindingError("beta_wedding_chat_collision")
    session.add(
        BetaSpaceBindingModel(
            workspace_id=workspace.id,
            connector_id=connector.id,
            tenant_ref=BETA_WEDDING_TENANT_REF,
            space_ref=BETA_WEDDING_SPACE_REF,
            installation_ref=BETA_WEDDING_INSTALLATION_REF,
            storage_ref=BETA_WEDDING_STORAGE_REF,
            telegram_chat_id=candidate.telegram_chat_id,
            subject_telegram_user_id=candidate.telegram_user_id,
            consent_sha256=hashlib.sha256(
                BETA_WEDDING_ACTIVATION_TEXT.encode()
            ).hexdigest(),
            state="consented",
            ready_at=now,
            consented_at=now,
        )
    )
    await session.commit()
    return "created"


async def beta_wedding_space_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    settings: Settings,
) -> dict[str, object]:
    """Return redacted wedding activation state and isolated event counts."""

    workspace_id = await session.scalar(
        select(Workspace.id).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    binding = None
    event_count = 0
    fact_count = 0
    if workspace_id is not None:
        binding = await session.scalar(
            select(BetaSpaceBindingModel).where(
                BetaSpaceBindingModel.workspace_id == workspace_id,
                BetaSpaceBindingModel.space_ref == BETA_WEDDING_SPACE_REF,
            )
        )
        if binding is not None:
            event_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            fact_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                        BetaSpaceEventModel.event_kind == "fact",
                    )
                )
                or 0
            )

    binding_state = binding.state if binding is not None else "absent"
    binding_live = binding_state == "consented"
    if not beta_space_production_ready(settings):
        state = "connector_not_ready"
    elif binding is None:
        state = "awaiting_binding"
    elif binding_live:
        state = "active"
    else:
        state = "revoked"

    return {
        "state": state,
        "gates": {
            "production": settings.gate_env == "production",
            "connector": settings.gate_beta_connector_enabled,
            "privacy_mode_confirmed": settings.gate_beta_privacy_mode_confirmed,
            "space_enabled": binding_live,
            "space_ready": binding_live,
            "delivery_enabled": False,
        },
        "configured": {
            "chat_binding": binding is not None,
            "subject_binding": binding is not None,
            "activation_digest": binding is not None,
        },
        "binding_state": binding_state,
        "production_ready": beta_space_production_ready(settings) and binding_live,
        "event_count": event_count,
        "fact_count": fact_count,
        "private_identifiers_exposed": False,
        "private_payload_exposed": False,
    }

