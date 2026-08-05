"""Owner-confirmed, fail-closed private binding for the Katya Beta Space."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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

BETA_BINDING_CLAIM_NOT_BEFORE = datetime(2026, 8, 5, 19, 17, 13, tzinfo=UTC)
BETA_BINDING_CANDIDATE_TEXT = "Подключить Бету: Зубы Кати"
BETA_BINDING_GROUP_TITLE = "Зубы Кати"
BETA_CONSENT_PROMPT = (
    "Катя, чтобы Бета сохраняла новые сообщения о зубах только в этой группе, "
    "отправь одним сообщением: «"
    + KATYA_CONSENT_TEXT
    + "». История до согласия не импортируется."
)


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
    """Accept only one exact, post-claim, subject-authored activation sentence."""

    if received_at.tzinfo is None or received_at < BETA_BINDING_CLAIM_NOT_BEFORE:
        return None
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict) or message.get("text") != BETA_BINDING_CANDIDATE_TEXT:
        return None
    chat = message.get("chat")
    actor = message.get("from")
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or chat.get("title") != BETA_BINDING_GROUP_TITLE
        or type(chat.get("id")) is not int
        or not isinstance(actor, dict)
        or actor.get("is_bot") is not False
        or type(actor.get("id")) is not int
        or actor.get("id") <= 0
    ):
        return None
    return BetaCandidate(
        receipt_id=receipt_id,
        update_id=update_id,
        telegram_chat_id=str(chat["id"]),
        telegram_user_id=str(actor["id"]),
        received_at=received_at,
    )


async def prepare_beta_katya_candidate_binding(
    session: AsyncSession, *, workspace_key: str, now: datetime
) -> BetaCandidateBindingResult:
    """Atomically bind exactly one private candidate without returning identifiers."""

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
    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(
                TelegramReceipt.bot_key == BETA_CONNECTOR_KEY,
                TelegramReceipt.received_at >= BETA_BINDING_CLAIM_NOT_BEFORE,
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
    if len(distinct) != 1 or len(candidates) != 1:
        raise BetaCandidateBindingError("beta_binding_candidate_ambiguous")
    candidate = candidates[0]
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
