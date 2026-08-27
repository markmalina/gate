"""Durable default-inactive exact route for an isolated AQUB Food Cube."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUB_FOOD_CONTEXT_ROUTE_SCOPE,
    AQUBTransportBinding,
)
from app.core.config import Settings
from app.infrastructure.aqub_installation import (
    AQUB_RECEIPT_BOT_KEY,
    AQUB_UNBOUND_ACE_USER_ID,
    aqub_receipt_signing_key,
)
from app.models.aqub import (
    AQUBFoodRoute,
    AQUBGeneralRoute,
    AQUBInstallationBinding,
)
from app.models.domain import TelegramReceipt, TelegramReceiptStatus


class AQUBFoodRouteStatus(StrEnum):
    HOLD = "hold"
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True, repr=False)
class AQUBFoodRouteEvidence:
    receipt_id: UUID
    group_chat_id: str = field(repr=False)
    actor_user_id: str = field(repr=False)
    bound_at: datetime


def _message_mapping(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    message = payload.get("message")
    return message if isinstance(message, Mapping) else None


def food_route_evidence_from_receipt(
    receipt: TelegramReceipt,
) -> AQUBFoodRouteEvidence | None:
    """Project one terminal owner message without exposing raw provider IDs."""

    if (
        receipt.bot_key != AQUB_RECEIPT_BOT_KEY
        or receipt.status != TelegramReceiptStatus.PROCESSED.value
        or receipt.processed_at is None
    ):
        return None
    message = _message_mapping(receipt.payload)
    if message is None:
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    chat_id = chat.get("id")
    actor_id = sender.get("id")
    if (
        chat.get("type") not in {"group", "supergroup"}
        or sender.get("is_bot") is True
        or type(chat_id) is not int
        or type(actor_id) is not int
    ):
        return None
    return AQUBFoodRouteEvidence(
        receipt_id=receipt.id,
        group_chat_id=str(chat_id),
        actor_user_id=str(actor_id),
        bound_at=receipt.processed_at,
    )


async def _route_conflict(
    session: AsyncSession,
    *,
    group_chat_id: str,
) -> str | None:
    installation = await session.scalar(
        select(AQUBInstallationBinding).where(
            AQUBInstallationBinding.bot_key == AQUB_RECEIPT_BOT_KEY
        )
    )
    if (
        isinstance(installation, AQUBInstallationBinding)
        and installation.group_chat_id == group_chat_id
    ):
        return "production"
    general = await session.scalar(
        select(AQUBGeneralRoute).where(
            AQUBGeneralRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
            AQUBGeneralRoute.group_chat_id == group_chat_id,
            AQUBGeneralRoute.status != "retired",
        )
    )
    return "general" if isinstance(general, AQUBGeneralRoute) else None


async def register_aqub_food_route_hold(
    session: AsyncSession,
    *,
    evidence_receipt_id: UUID,
) -> AQUBFoodRoute:
    """Register one receipt-backed route on hold; never activate it."""

    receipt = await session.scalar(
        select(TelegramReceipt)
        .where(TelegramReceipt.id == evidence_receipt_id)
        .with_for_update()
    )
    if not isinstance(receipt, TelegramReceipt):
        raise ValueError("aqub_food_route_evidence_missing")
    evidence = food_route_evidence_from_receipt(receipt)
    if evidence is None:
        raise ValueError("aqub_food_route_evidence_invalid")
    conflict = await _route_conflict(
        session,
        group_chat_id=evidence.group_chat_id,
    )
    if conflict is not None:
        raise ValueError(f"aqub_food_route_{conflict}_conflict")
    existing = await session.scalar(
        select(AQUBFoodRoute).where(
            AQUBFoodRoute.evidence_receipt_id == evidence.receipt_id
        )
    )
    if isinstance(existing, AQUBFoodRoute):
        if (
            existing.bot_key != AQUB_RECEIPT_BOT_KEY
            or existing.group_chat_id != evidence.group_chat_id
            or existing.actor_user_id != evidence.actor_user_id
            or existing.bound_at != evidence.bound_at
        ):
            raise RuntimeError("aqub_food_route_evidence_conflict")
        return existing
    live_group = await session.scalar(
        select(AQUBFoodRoute).where(
            AQUBFoodRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
            AQUBFoodRoute.group_chat_id == evidence.group_chat_id,
            AQUBFoodRoute.status != AQUBFoodRouteStatus.RETIRED.value,
        )
    )
    if isinstance(live_group, AQUBFoodRoute):
        raise RuntimeError("aqub_food_route_group_conflict")
    route = AQUBFoodRoute(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        group_chat_id=evidence.group_chat_id,
        actor_user_id=evidence.actor_user_id,
        evidence_receipt_id=evidence.receipt_id,
        status=AQUBFoodRouteStatus.HOLD.value,
        bound_at=evidence.bound_at,
    )
    session.add(route)
    await session.commit()
    return route


async def transition_aqub_food_route(
    session: AsyncSession,
    *,
    route_id: UUID,
    expected_status: AQUBFoodRouteStatus,
    next_status: AQUBFoodRouteStatus,
    occurred_at: datetime,
) -> AQUBFoodRoute:
    """Apply one legal lifecycle transition with an exact status fence."""

    legal = {
        (AQUBFoodRouteStatus.HOLD, AQUBFoodRouteStatus.ACTIVE),
        (AQUBFoodRouteStatus.ACTIVE, AQUBFoodRouteStatus.HOLD),
        (AQUBFoodRouteStatus.HOLD, AQUBFoodRouteStatus.RETIRED),
    }
    if (expected_status, next_status) not in legal:
        raise ValueError("aqub_food_route_transition_invalid")
    route = await session.scalar(
        select(AQUBFoodRoute)
        .where(AQUBFoodRoute.id == route_id)
        .with_for_update()
    )
    if not isinstance(route, AQUBFoodRoute):
        raise ValueError("aqub_food_route_missing")
    if route.status != expected_status.value:
        raise RuntimeError("aqub_food_route_status_conflict")
    if next_status is AQUBFoodRouteStatus.ACTIVE:
        conflict = await _route_conflict(
            session,
            group_chat_id=route.group_chat_id,
        )
        if conflict is not None:
            raise ValueError(f"aqub_food_route_{conflict}_conflict")
    route.status = next_status.value
    if next_status is AQUBFoodRouteStatus.ACTIVE:
        route.activated_at = occurred_at
        route.retired_at = None
    elif next_status is AQUBFoodRouteStatus.HOLD:
        route.activated_at = None
        route.retired_at = None
    else:
        route.activated_at = None
        route.retired_at = occurred_at
    await session.commit()
    return route


async def load_active_aqub_food_binding(
    session: AsyncSession,
    settings: Settings,
    *,
    chat_id: str,
) -> AQUBTransportBinding | None:
    """Resolve one active isolated Food group with its exact owner."""

    route = await session.scalar(
        select(AQUBFoodRoute).where(
            AQUBFoodRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
            AQUBFoodRoute.group_chat_id == chat_id,
            AQUBFoodRoute.status == AQUBFoodRouteStatus.ACTIVE.value,
        )
    )
    if not isinstance(route, AQUBFoodRoute):
        return None
    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=route.group_chat_id,
        ace_user_id=AQUB_UNBOUND_ACE_USER_ID,
        alpha_user_id=route.actor_user_id,
        ready_at=route.activated_at,
        receipt_signing_key=aqub_receipt_signing_key(settings),
        route_scope=AQUB_FOOD_CONTEXT_ROUTE_SCOPE,
    )


async def select_aqub_food_inbound_binding(
    session: AsyncSession,
    settings: Settings,
    *,
    fallback_binding: AQUBTransportBinding,
    chat_id: str,
) -> AQUBTransportBinding:
    """Select an active exact Food route or preserve the production fallback."""

    route = await load_active_aqub_food_binding(
        session,
        settings,
        chat_id=chat_id,
    )
    return route if route is not None else fallback_binding
