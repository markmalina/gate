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
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
)

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
    received_count: int = 0
    processing_count: int = 0
    delivered_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    last_error_code: str | None = None

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
            "received_count": self.received_count,
            "processing_count": self.processing_count,
            "delivered_count": self.delivered_count,
            "retry_count": self.retry_count,
            "dead_letter_count": self.dead_letter_count,
            "last_error_code": self.last_error_code,
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
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
    queue = await _telegram_voice_queue_summary(
        session,
        period_start=period_start,
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
        received_count=queue.received_count,
        processing_count=queue.processing_count,
        delivered_count=queue.delivered_count,
        retry_count=queue.retry_count,
        dead_letter_count=queue.dead_letter_count,
        last_error_code=queue.last_error_code,
    )


@dataclass(frozen=True, slots=True)
class _TelegramVoiceQueueSummary:
    received_count: int
    processing_count: int
    delivered_count: int
    retry_count: int
    dead_letter_count: int
    last_error_code: str | None


async def _telegram_voice_queue_summary(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> _TelegramVoiceQueueSummary:
    voice_payload = or_(
        TelegramReceipt.payload.op("#>")("{business_message,voice}").is_not(None),
        TelegramReceipt.payload.op("#>")("{message,voice}").is_not(None),
    )
    base_filters = (
        TelegramReceipt.bot_key == "telegram_business",
        TelegramReceipt.received_at >= period_start,
        voice_payload,
    )
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(
                    TelegramOutboxEvent.status.in_(
                        (
                            TelegramOutboxStatus.PENDING.value,
                            TelegramOutboxStatus.PROCESSING.value,
                        )
                    )
                ),
                func.count().filter(
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.DELIVERED.value
                ),
                func.count().filter(
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.RETRY_SCHEDULED.value
                ),
                func.count().filter(
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.DEAD_LETTER.value
                ),
            )
            .select_from(TelegramReceipt)
            .join(
                TelegramOutboxEvent,
                TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
            )
            .where(*base_filters)
        )
    ).one()
    last_error_code = (
        await session.execute(
            select(TelegramOutboxEvent.last_error_code)
            .select_from(TelegramOutboxEvent)
            .join(
                TelegramReceipt,
                TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
            )
            .where(
                *base_filters,
                TelegramOutboxEvent.last_error_code.is_not(None),
            )
            .order_by(
                TelegramOutboxEvent.last_error_at.desc(),
                TelegramOutboxEvent.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return _TelegramVoiceQueueSummary(
        received_count=int(row[0] or 0),
        processing_count=int(row[1] or 0),
        delivered_count=int(row[2] or 0),
        retry_count=int(row[3] or 0),
        dead_letter_count=int(row[4] or 0),
        last_error_code=last_error_code,
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
