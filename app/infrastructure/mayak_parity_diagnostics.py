"""Owner-only exact-ID diagnostics for post-Mayak text parity."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.canonical_messaging import WorkRoute
from app.application.telegram_business_text_parity import (
    TELEGRAM_BUSINESS_TEXT_PARITY_POLICY_VERSION,
)
from app.models.agent import AgentSessionModel, AgentTurnModel
from app.models.domain import Message

JsonObject = dict[str, Any]


async def exact_mayak_parity_join(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    canonical_message_id: UUID,
    dialog_id: UUID,
    account_id: UUID,
    project_ids: frozenset[UUID],
    legacy_message_id: UUID | None,
    observed_reply_task_count: int,
) -> JsonObject:
    """Join one canonical fact to exactly one scoped Mayak turn, or fail closed."""

    if legacy_message_id is None or not project_ids:
        return _ambiguous(observed_reply_task_count)
    legacy_ids = list(
        (
            await session.scalars(
                select(Message.id).where(
                    Message.id == legacy_message_id,
                    Message.canonical_message_id == canonical_message_id,
                    Message.account_id == account_id,
                    Message.project_id.in_(project_ids),
                )
            )
        ).all()
    )
    if legacy_ids != [legacy_message_id]:
        return _ambiguous(observed_reply_task_count)

    turns = (
        await session.execute(
            select(AgentTurnModel, AgentSessionModel)
            .join(
                AgentSessionModel,
                (AgentSessionModel.id == AgentTurnModel.session_id)
                & (
                    AgentSessionModel.workspace_id
                    == AgentTurnModel.workspace_id
                ),
            )
            .where(
                AgentTurnModel.workspace_id == workspace_id,
                AgentTurnModel.trigger_message_id == canonical_message_id,
                AgentSessionModel.dialog_id == dialog_id,
            )
        )
    ).all()
    if len(turns) != 1:
        return _ambiguous(observed_reply_task_count)

    turn, _agent_session = turns[0]
    expected = 0
    classification = (
        "expected_zero_reply_task"
        if observed_reply_task_count == expected
        else "unexpected_reply_task"
    )
    return {
        "exact_join": True,
        "mayak_turn_ref": _opaque_ref(turn.id),
        "mayak_disposition": "processed",
        "effective_route": WorkRoute.AGENT_TURN.value,
        "reply_task_expected": expected,
        "observed_reply_task_count": observed_reply_task_count,
        "parity_policy_version": (
            TELEGRAM_BUSINESS_TEXT_PARITY_POLICY_VERSION
        ),
        "classification": classification,
        "historical_false_positive": classification
        == "expected_zero_reply_task",
    }


def _ambiguous(observed_reply_task_count: int) -> JsonObject:
    return {
        "exact_join": False,
        "mayak_turn_ref": None,
        "mayak_disposition": "unknown",
        "effective_route": None,
        "reply_task_expected": None,
        "observed_reply_task_count": observed_reply_task_count,
        "parity_policy_version": (
            TELEGRAM_BUSINESS_TEXT_PARITY_POLICY_VERSION
        ),
        "classification": "ambiguous",
        "historical_false_positive": False,
    }


def _opaque_ref(value: UUID) -> str:
    return sha256(str(value).encode()).hexdigest()[:16]
