"""Owner-confirmed, fail-closed private binding for the Katya Beta Space."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.beta_space_intake import KATYA_CONSENT_TEXT
from app.models.beta_space import BetaSpaceBindingModel
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import TelegramReceipt

from .beta_space import (
    BETA_CONNECTOR_KEY,
    BETA_INSTALLATION_REF,
    BETA_SPACE_REF,
    BETA_STORAGE_REF,
    BETA_TENANT_REF,
)

BETA_BINDING_EVIDENCE_NOT_BEFORE = datetime(2026, 8, 5, tzinfo=UTC)
BETA_BINDING_EVIDENCE_TEXT = (
    "Я согласна, чтобы Бета собирала и систематизировала размещённую в этой "
    "группе информацию о моих зубах и лечении."
)
BETA_BINDING_GROUP_TITLE = "Зубы Кати"
BETA_CONSENT_PROMPT = (
    "Катя, ты согласна, чтобы Бета собирала и систематизировала новые сообщения "
    "о твоих зубах и лечении из этой группы?\n\n"
    "История сообщений до согласия не загружается."
)
BETA_CONSENT_REPLY_MARKUP = {
    "inline_keyboard": [
        [
            {
                "text": "Да, согласна",
                "callback_data": "beta:katya:consent:yes",
            },
            {
                "text": "Нет",
                "callback_data": "beta:katya:consent:no",
            },
        ]
    ]
}


class BetaCandidateBindingError(RuntimeError):
    """Reject ambiguous or conflicting private activation candidates."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BetaCandidate:
    receipt_id: uuid.UUID
    update_id: int
    telegram_chat_id: str = field(repr=False)
    telegram_user_id: str = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class BetaGroupCandidate:
    telegram_chat_id: str = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class BetaCallbackOwnerCandidate:
    telegram_chat_id: str = field(repr=False)
    telegram_user_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BetaCandidateBindingResult:
    state: str
    candidate_count: int
    binding_created: bool
    prompt_required: bool
    telegram_chat_id: str | None = field(default=None, repr=False)

    def redacted(self, *, prompt_delivery: str = "not_attempted") -> dict[str, object]:
        return {
            "state": self.state,
            "candidate_count": self.candidate_count,
            "binding_created": self.binding_created,
            "prompt_required": self.prompt_required,
            "prompt_delivery": prompt_delivery,
            "private_identifiers_exposed": False,
            "private_payload_exposed": False,
            "consent_digest_exposed": False,
        }


def extract_beta_binding_candidate(
    *, receipt_id: uuid.UUID, update_id: int, payload: object, received_at: datetime
) -> BetaCandidate | None:
    """Use the exact prior consent only as private group/subject identity evidence."""

    if received_at.tzinfo is None or received_at < BETA_BINDING_EVIDENCE_NOT_BEFORE:
        return None
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict) or message.get("text") != BETA_BINDING_EVIDENCE_TEXT:
        return None
    chat = message.get("chat")
    actor = message.get("from")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    actor_id = actor.get("id") if isinstance(actor, dict) else None
    actor_first_name = actor.get("first_name") if isinstance(actor, dict) else None
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or chat.get("title") != BETA_BINDING_GROUP_TITLE
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or not isinstance(actor, dict)
        or actor.get("is_bot") is not False
        or not isinstance(actor_first_name, str)
        or actor_first_name.strip().casefold() != "катя"
        or not isinstance(actor_id, int)
        or isinstance(actor_id, bool)
        or actor_id <= 0
    ):
        return None
    return BetaCandidate(
        receipt_id=receipt_id,
        update_id=update_id,
        telegram_chat_id=str(chat_id),
        telegram_user_id=str(actor_id),
        received_at=received_at,
    )


def extract_beta_group_candidate(
    *, payload: object, received_at: datetime
) -> BetaGroupCandidate | None:
    """Select only the exact existing group from a durable bot-membership receipt."""

    if received_at.tzinfo is None:
        return None
    if not isinstance(payload, dict):
        return None
    membership = payload.get("my_chat_member")
    if not isinstance(membership, dict):
        return None
    chat = membership.get("chat")
    new_member = membership.get("new_chat_member")
    bot = new_member.get("user") if isinstance(new_member, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or chat.get("title") != BETA_BINDING_GROUP_TITLE
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or not isinstance(new_member, dict)
        or new_member.get("status") not in {"member", "administrator"}
        or not isinstance(bot, dict)
        or bot.get("is_bot") is not True
    ):
        return None
    return BetaGroupCandidate(
        telegram_chat_id=str(chat_id),
        received_at=received_at,
    )


def extract_beta_callback_owner_candidate(
    payload: object,
) -> BetaCallbackOwnerCandidate | None:
    """Extract one exact Katya button callback without trusting it as consent yet."""

    if not isinstance(payload, dict):
        return None
    callback = payload.get("callback_query")
    if not isinstance(callback, dict) or callback.get("data") not in {
        "beta:katya:consent:yes",
        "beta:katya:consent:no",
    }:
        return None
    actor = callback.get("from")
    message = callback.get("message")
    chat = message.get("chat") if isinstance(message, dict) else None
    actor_id = actor.get("id") if isinstance(actor, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    first_name = actor.get("first_name") if isinstance(actor, dict) else None
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or chat.get("title") != BETA_BINDING_GROUP_TITLE
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or not isinstance(actor, dict)
        or actor.get("is_bot") is not False
        or not isinstance(first_name, str)
        or not first_name.strip()
        or first_name.strip().casefold().split(maxsplit=1)[0] != "катя"
        or not isinstance(actor_id, int)
        or isinstance(actor_id, bool)
        or actor_id <= 0
    ):
        return None
    return BetaCallbackOwnerCandidate(
        telegram_chat_id=str(chat_id),
        telegram_user_id=str(actor_id),
    )


def extract_beta_katya_creator(administrators: object) -> str | None:
    """Return one exact non-bot Katya creator, otherwise fail closed."""

    if not isinstance(administrators, list):
        return None
    matches: list[int] = []
    for administrator in administrators:
        if not isinstance(administrator, dict) or administrator.get("status") != "creator":
            continue
        actor = administrator.get("user")
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        first_name = actor.get("first_name") if isinstance(actor, dict) else None
        if (
            isinstance(actor, dict)
            and actor.get("is_bot") is False
            and isinstance(first_name, str)
            and bool(first_name.strip())
            and first_name.strip().casefold().split(maxsplit=1)[0] == "катя"
            and isinstance(actor_id, int)
            and not isinstance(actor_id, bool)
            and actor_id > 0
        ):
            matches.append(actor_id)
    return str(matches[0]) if len(set(matches)) == 1 else None


async def find_beta_katya_group_candidates(
    session: AsyncSession,
) -> list[BetaGroupCandidate]:
    """Return a bounded newest-first set of exact-title membership candidates."""

    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(TelegramReceipt.bot_key == BETA_CONNECTOR_KEY)
            .order_by(TelegramReceipt.received_at, TelegramReceipt.id)
        )
    ).all()
    by_chat: dict[str, BetaGroupCandidate] = {}
    for receipt in receipts:
        candidate = extract_beta_group_candidate(
            payload=receipt.payload,
            received_at=receipt.received_at,
        )
        if candidate is not None:
            by_chat[candidate.telegram_chat_id] = candidate
    if not by_chat:
        raise BetaCandidateBindingError("beta_binding_group_candidate_missing")
    if len(by_chat) > 3:
        raise BetaCandidateBindingError("beta_binding_group_candidate_ambiguous")
    return sorted(
        by_chat.values(),
        key=lambda candidate: candidate.received_at,
        reverse=True,
    )


async def prepare_beta_katya_candidate_binding(
    session: AsyncSession,
    *,
    workspace_key: str,
    now: datetime,
    telegram_chat_id: str | None = None,
    telegram_user_id: str | None = None,
) -> BetaCandidateBindingResult:
    """Atomically bind one distinct private identity without returning identifiers."""

    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise BetaCandidateBindingError("beta_binding_workspace_missing")
    existing = await session.scalar(
        select(BetaSpaceBindingModel)
        .where(
            BetaSpaceBindingModel.workspace_id == workspace.id,
            BetaSpaceBindingModel.space_ref == BETA_SPACE_REF,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.state == "revoked":
            raise BetaCandidateBindingError("beta_binding_revoked")
        return BetaCandidateBindingResult("already_bound", 1, False, False)
    connector = await session.scalar(
        select(TransportConnector).where(
            TransportConnector.workspace_id == workspace.id,
            TransportConnector.connector_key == BETA_CONNECTOR_KEY,
            TransportConnector.is_enabled.is_(True),
        )
    )
    if connector is None:
        raise BetaCandidateBindingError("beta_binding_connector_missing")
    if (telegram_chat_id is None) != (telegram_user_id is None):
        raise BetaCandidateBindingError("beta_binding_private_identity_incomplete")
    if telegram_chat_id is not None and telegram_user_id is not None:
        if not telegram_chat_id or not telegram_user_id:
            raise BetaCandidateBindingError("beta_binding_private_identity_invalid")
        candidates = [
            BetaCandidate(
                receipt_id=uuid.UUID(int=0),
                update_id=0,
                telegram_chat_id=telegram_chat_id,
                telegram_user_id=telegram_user_id,
                received_at=now,
            )
        ]
    else:
        receipts = (
            await session.scalars(
                select(TelegramReceipt)
                .where(
                    TelegramReceipt.bot_key == BETA_CONNECTOR_KEY,
                    TelegramReceipt.received_at >= BETA_BINDING_EVIDENCE_NOT_BEFORE,
                )
                .order_by(TelegramReceipt.received_at, TelegramReceipt.id)
                .with_for_update()
            )
        ).all()
        candidates = [
            candidate
            for receipt in receipts
            if (
                candidate := extract_beta_binding_candidate(
                    receipt_id=receipt.id,
                    update_id=receipt.update_id,
                    payload=receipt.payload,
                    received_at=receipt.received_at,
                )
            )
            is not None
        ]
    distinct = {
        (candidate.telegram_chat_id, candidate.telegram_user_id)
        for candidate in candidates
    }
    if not distinct:
        return BetaCandidateBindingResult("awaiting_candidate", 0, False, False)
    if len(distinct) != 1:
        raise BetaCandidateBindingError("beta_binding_candidate_ambiguous")
    candidate = candidates[-1]
    collision = await session.scalar(
        select(BetaSpaceBindingModel.id).where(
            BetaSpaceBindingModel.connector_id == connector.id,
            BetaSpaceBindingModel.telegram_chat_id == candidate.telegram_chat_id,
        )
    )
    if collision is not None:
        raise BetaCandidateBindingError("beta_binding_chat_collision")
    session.add(
        BetaSpaceBindingModel(
            workspace_id=workspace.id,
            connector_id=connector.id,
            tenant_ref=BETA_TENANT_REF,
            space_ref=BETA_SPACE_REF,
            installation_ref=BETA_INSTALLATION_REF,
            storage_ref=BETA_STORAGE_REF,
            telegram_chat_id=candidate.telegram_chat_id,
            subject_telegram_user_id=candidate.telegram_user_id,
            consent_sha256=hashlib.sha256(KATYA_CONSENT_TEXT.encode()).hexdigest(),
            state="ready",
            ready_at=now,
        )
    )
    await session.commit()
    return BetaCandidateBindingResult(
        "bound_awaiting_consent",
        1,
        True,
        True,
        candidate.telegram_chat_id,
    )


async def revoke_beta_katya_binding_after_unknown_prompt(
    session: AsyncSession, *, workspace_key: str
) -> None:
    """Make an ambiguous prompt delivery terminal and non-retryable."""

    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise BetaCandidateBindingError("beta_binding_workspace_missing")
    binding = await session.scalar(
        select(BetaSpaceBindingModel)
        .where(
            BetaSpaceBindingModel.workspace_id == workspace.id,
            BetaSpaceBindingModel.space_ref == BETA_SPACE_REF,
            BetaSpaceBindingModel.state == "ready",
        )
        .with_for_update()
    )
    if binding is None:
        raise BetaCandidateBindingError("beta_binding_prompt_state_conflict")
    binding.state = "revoked"
    await session.commit()
