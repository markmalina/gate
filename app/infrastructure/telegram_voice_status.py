"""Redacted operational status for dormant Telegram voice transcription."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.billing import BillingProduct, BudgetReservationState
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
)
from app.models.canonical import Workspace

JsonObject = dict[str, Any]
_VOICE_SCOPE_KIND = "telegram_connector"
_VOICE_SCOPE_REF = "telegram_business"


@dataclass(frozen=True, slots=True)
class TelegramVoiceOperationalStatus:
    state: str
    enabled: bool
    api_key_configured: bool
    model: str
    entitlement_active: bool
    period_key: str
    hard_limit_microusd: int
    settled_microusd: int
    reserved_microusd: int
    remaining_microusd: int
    settled_count: int
    open_count: int
    unknown_count: int
    reconciliation_required: bool
    telegram_delivery_allowed: bool = False

    def payload(self) -> JsonObject:
        return {
            "state": self.state,
            "enabled": self.enabled,
            "api_key_configured": self.api_key_configured,
            "model": self.model,
            "entitlement_active": self.entitlement_active,
            "period_key": self.period_key,
            "hard_limit_microusd": self.hard_limit_microusd,
            "settled_microusd": self.settled_microusd,
            "reserved_microusd": self.reserved_microusd,
            "remaining_microusd": self.remaining_microusd,
            "settled_count": self.settled_count,
            "open_count": self.open_count,
            "unknown_count": self.unknown_count,
            "reconciliation_required": self.reconciliation_required,
            "telegram_delivery_allowed": self.telegram_delivery_allowed,
        }


async def telegram_voice_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    enabled: bool,
    api_key_configured: bool,
    model: str,
    now: datetime,
) -> TelegramVoiceOperationalStatus:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("telegram_voice_status_time_invalid")
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise ValueError("telegram_voice_status_workspace_not_active")

    entitlements = (
        await session.scalars(
            select(BillingEntitlementModel).where(
                BillingEntitlementModel.workspace_id == workspace.id,
                BillingEntitlementModel.product == BillingProduct.GATE.value,
                BillingEntitlementModel.scope_kind == _VOICE_SCOPE_KIND,
                BillingEntitlementModel.scope_ref == _VOICE_SCOPE_REF,
                BillingEntitlementModel.state == "active",
                BillingEntitlementModel.valid_from <= now,
                or_(
                    BillingEntitlementModel.valid_until.is_(None),
                    BillingEntitlementModel.valid_until > now,
                ),
            )
        )
    ).all()
    entitlement_active = (
        len(entitlements) == 1 and entitlements[0].hard_limit_microusd is not None
    )
    hard_limit_microusd = (
        int(entitlements[0].hard_limit_microusd or 0)
        if entitlement_active
        else 0
    )
    period_key = now.strftime("%Y-%m")
    settled_expression = case(
        (
            BillingBudgetReservationModel.state
            == BudgetReservationState.SETTLED.value,
            BillingBudgetReservationModel.settled_microusd,
        ),
        else_=0,
    )
    reserved_expression = case(
        (
            BillingBudgetReservationModel.state.in_(
                (
                    BudgetReservationState.RESERVED.value,
                    BudgetReservationState.UNKNOWN.value,
                )
            ),
            BillingBudgetReservationModel.reserved_microusd,
        ),
        else_=0,
    )
    summary = (
        await session.execute(
            select(
                func.coalesce(func.sum(settled_expression), 0),
                func.coalesce(func.sum(reserved_expression), 0),
                func.count().filter(
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.SETTLED.value
                ),
                func.count().filter(
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.RESERVED.value
                ),
                func.count().filter(
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.UNKNOWN.value
                ),
            ).where(
                BillingBudgetReservationModel.workspace_id == workspace.id,
                BillingBudgetReservationModel.product == BillingProduct.GATE.value,
                BillingBudgetReservationModel.scope_kind == _VOICE_SCOPE_KIND,
                BillingBudgetReservationModel.scope_ref == _VOICE_SCOPE_REF,
                BillingBudgetReservationModel.operation == "transcription",
                BillingBudgetReservationModel.period_key == period_key,
            )
        )
    ).one()
    settled_microusd = int(summary[0] or 0)
    reserved_microusd = int(summary[1] or 0)
    settled_count = int(summary[2] or 0)
    open_count = int(summary[3] or 0)
    unknown_count = int(summary[4] or 0)
    remaining_microusd = max(
        hard_limit_microusd - settled_microusd - reserved_microusd,
        0,
    )
    state = classify_telegram_voice_status(
        enabled=enabled,
        api_key_configured=api_key_configured,
        entitlement_active=entitlement_active,
        remaining_microusd=remaining_microusd,
        open_count=open_count,
        unknown_count=unknown_count,
    )
    return TelegramVoiceOperationalStatus(
        state=state,
        enabled=enabled,
        api_key_configured=api_key_configured,
        model=model,
        entitlement_active=entitlement_active,
        period_key=period_key,
        hard_limit_microusd=hard_limit_microusd,
        settled_microusd=settled_microusd,
        reserved_microusd=reserved_microusd,
        remaining_microusd=remaining_microusd,
        settled_count=settled_count,
        open_count=open_count,
        unknown_count=unknown_count,
        reconciliation_required=unknown_count > 0,
    )


def classify_telegram_voice_status(
    *,
    enabled: bool,
    api_key_configured: bool,
    entitlement_active: bool,
    remaining_microusd: int,
    open_count: int,
    unknown_count: int,
) -> str:
    if min(remaining_microusd, open_count, unknown_count) < 0:
        raise ValueError("telegram_voice_status_value_invalid")
    if unknown_count:
        return "reconciliation_required"
    if not enabled:
        return "disabled"
    if not api_key_configured or not entitlement_active:
        return "configuration_incomplete"
    if remaining_microusd == 0:
        return "budget_exhausted"
    if open_count:
        return "settlement_pending"
    return "ready"
