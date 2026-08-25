"""Durable default-inactive exact route for the AQUB General Cube."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBTransportBinding,
    AQUBTransportContentKind,
)
from app.core.config import Settings
from app.infrastructure.aqub_installation import (
    AQUB_RECEIPT_BOT_KEY,
    AQUB_UNBOUND_ACE_USER_ID,
    aqub_receipt_signing_key,
)
from app.models.aqub import AQUBFoodRoute, AQUBGeneralRoute, AQUBInstallationBinding
from app.models.domain import TelegramReceipt, TelegramReceiptStatus


class AQUBGeneralRouteStatus(StrEnum):
    HOLD = "hold"
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True, repr=False)
class AQUBGeneralRouteEvidence:
    receipt_id: UUID
    group_chat_id: str = field(repr=False)
    actor_user_id: str = field(repr=False)
    bound_at: datetime


async def _food_route_conflicts(session: AsyncSession, *, group_chat_id: str) -> bool:
    route = await session.scalar(
        select(AQUBFoodRoute).where(
            AQUBFoodRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
            AQUBFoodRoute.group_chat_id == group_chat_id,
            AQUBFoodRoute.status != "retired",
        )
    )
    return isinstance(route, AQUBFoodRoute)


def _message_mapping(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    message = payload.get("message")
    return message if isinstance(message, Mapping) else None


def general_route_evidence_from_receipt(
    receipt: TelegramReceipt,
) -> AQUBGeneralRouteEvidence | None:
    """Project an operator-selected terminal receipt without exposing raw IDs."""

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
    return AQUBGeneralRouteEvidence(
        receipt_id=receipt.id,
        group_chat_id=str(chat_id),
        actor_user_id=str(actor_id),
        bound_at=receipt.processed_at,
    )


async def register_aqub_general_route_hold(
    session: AsyncSession,
    *,
    evidence_receipt_id: UUID,
) -> AQUBGeneralRoute:
    """Register one immutable receipt-backed route on hold; never activate it."""

    receipt = await session.scalar(
        select(TelegramReceipt).where(TelegramReceipt.id == evidence_receipt_id).with_for_update()
    )
    if not isinstance(receipt, TelegramReceipt):
        raise ValueError("aqub_general_route_evidence_missing")
    evidence = general_route_evidence_from_receipt(receipt)
    if evidence is None:
        raise ValueError("aqub_general_route_evidence_invalid")
    food_binding = await session.scalar(
        select(AQUBInstallationBinding).where(
            AQUBInstallationBinding.bot_key == AQUB_RECEIPT_BOT_KEY
        )
    )
    if (
        isinstance(food_binding, AQUBInstallationBinding)
        and food_binding.group_chat_id == evidence.group_chat_id
    ):
        raise ValueError("aqub_general_route_food_group_conflict")
    if await _food_route_conflicts(session, group_chat_id=evidence.group_chat_id):
        raise ValueError("aqub_general_route_food_route_conflict")
    existing = await session.scalar(
        select(AQUBGeneralRoute).where(AQUBGeneralRoute.evidence_receipt_id == evidence.receipt_id)
    )
    if isinstance(existing, AQUBGeneralRoute):
        if (
            existing.bot_key != AQUB_RECEIPT_BOT_KEY
            or existing.group_chat_id != evidence.group_chat_id
            or existing.actor_user_id != evidence.actor_user_id
            or existing.bound_at != evidence.bound_at
        ):
            raise RuntimeError("aqub_general_route_evidence_conflict")
        return existing
    route = AQUBGeneralRoute(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        group_chat_id=evidence.group_chat_id,
        actor_user_id=evidence.actor_user_id,
        evidence_receipt_id=evidence.receipt_id,
        status=AQUBGeneralRouteStatus.HOLD.value,
        bound_at=evidence.bound_at,
    )
    session.add(route)
    await session.commit()
    return route


async def transition_aqub_general_route(
    session: AsyncSession,
    *,
    route_id: UUID,
    expected_status: AQUBGeneralRouteStatus,
    next_status: AQUBGeneralRouteStatus,
    occurred_at: datetime,
) -> AQUBGeneralRoute:
    """Apply the only legal lifecycle transitions with an exact status fence."""

    legal = {
        (AQUBGeneralRouteStatus.HOLD, AQUBGeneralRouteStatus.ACTIVE),
        (AQUBGeneralRouteStatus.ACTIVE, AQUBGeneralRouteStatus.HOLD),
        (AQUBGeneralRouteStatus.HOLD, AQUBGeneralRouteStatus.RETIRED),
    }
    if (expected_status, next_status) not in legal:
        raise ValueError("aqub_general_route_transition_invalid")
    route = await session.scalar(
        select(AQUBGeneralRoute).where(AQUBGeneralRoute.id == route_id).with_for_update()
    )
    if not isinstance(route, AQUBGeneralRoute):
        raise ValueError("aqub_general_route_missing")
    if route.status != expected_status.value:
        raise RuntimeError("aqub_general_route_status_conflict")
    if next_status is AQUBGeneralRouteStatus.ACTIVE:
        food_binding = await session.scalar(
            select(AQUBInstallationBinding).where(
                AQUBInstallationBinding.bot_key == AQUB_RECEIPT_BOT_KEY
            )
        )
        if (
            isinstance(food_binding, AQUBInstallationBinding)
            and food_binding.group_chat_id == route.group_chat_id
        ):
            raise ValueError("aqub_general_route_food_group_conflict")
        if await _food_route_conflicts(session, group_chat_id=route.group_chat_id):
            raise ValueError("aqub_general_route_food_route_conflict")
    route.status = next_status.value
    if next_status is AQUBGeneralRouteStatus.ACTIVE:
        route.activated_at = occurred_at
        route.retired_at = None
    elif next_status is AQUBGeneralRouteStatus.HOLD:
        route.activated_at = None
        route.retired_at = None
    else:
        route.activated_at = None
        route.retired_at = occurred_at
    await session.commit()
    return route


async def load_active_aqub_general_binding(
    session: AsyncSession,
    settings: Settings,
    *,
    chat_id: str,
) -> AQUBTransportBinding | None:
    """Resolve an active General group; the returned binding fences its exact actor."""

    route = await session.scalar(
        select(AQUBGeneralRoute).where(
            AQUBGeneralRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
            AQUBGeneralRoute.group_chat_id == chat_id,
            AQUBGeneralRoute.status == AQUBGeneralRouteStatus.ACTIVE.value,
        )
    )
    if not isinstance(route, AQUBGeneralRoute):
        return None
    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=route.group_chat_id,
        ace_user_id=AQUB_UNBOUND_ACE_USER_ID,
        alpha_user_id=route.actor_user_id,
        ready_at=route.activated_at,
        receipt_signing_key=aqub_receipt_signing_key(settings),
    )


async def select_aqub_inbound_binding(
    session: AsyncSession,
    settings: Settings,
    *,
    food_binding: AQUBTransportBinding,
    chat_id: str,
    actor_id: str,
    content_kind: AQUBTransportContentKind,
) -> AQUBTransportBinding:
    """Route General text exactly and suppress its unsupported media fail-closed."""

    general = await load_active_aqub_general_binding(
        session,
        settings,
        chat_id=chat_id,
    )
    if general is not None and content_kind is not AQUBTransportContentKind.TEXT:
        # The General Cube v1 is text-only. A disabled binding makes the live
        # adapter reject before Gate's Food receipt, queue, or AQUB runtime can
        # observe media from an active General group, regardless of actor.
        return replace(general, enabled=False)
    if general is not None and general.alpha_user_id != actor_id:
        # Preserve the matched General binding so the existing live admission
        # denies this actor instead of falling through to Food owner semantics.
        return general
    return general if general is not None else food_binding
