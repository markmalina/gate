"""Transactional PostgreSQL hard-budget reservation adapter."""

from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.billing import (
    BillingDomainError,
    BudgetAdmission,
    BudgetReservation,
    BudgetReservationState,
    EntitlementState,
    ReserveBudgetCommand,
)
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
)
from app.models.canonical import Workspace


class SqlBillingBudgetService:
    """Serialize admission by workspace before inserting one reservation."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission:
        _period(period_key)
        async with self._sessions() as session, session.begin():
            workspace = await session.scalar(
                select(Workspace)
                .where(
                    Workspace.key == command.attribution.workspace_key,
                    Workspace.is_active.is_(True),
                )
                .with_for_update()
            )
            if workspace is None:
                raise BillingDomainError("billing_workspace_not_active")

            existing = await session.scalar(
                select(BillingBudgetReservationModel)
                .where(
                    BillingBudgetReservationModel.workspace_id == workspace.id,
                    BillingBudgetReservationModel.reservation_key
                    == command.reservation_key,
                )
                .with_for_update()
            )
            if existing is not None:
                _require_exact_replay(existing, command, period_key=period_key)
                return BudgetAdmission(
                    allowed=False,
                    reason_code="billing_reservation_replayed",
                )

            entitlements = (
                await session.scalars(
                    select(BillingEntitlementModel).where(
                        BillingEntitlementModel.workspace_id == workspace.id,
                        BillingEntitlementModel.product
                        == command.attribution.product.value,
                        BillingEntitlementModel.scope_kind
                        == command.attribution.scope_kind,
                        BillingEntitlementModel.scope_ref
                        == command.attribution.scope_ref,
                        BillingEntitlementModel.state == EntitlementState.ACTIVE.value,
                        BillingEntitlementModel.valid_from <= command.occurred_at,
                        or_(
                            BillingEntitlementModel.valid_until.is_(None),
                            BillingEntitlementModel.valid_until > command.occurred_at,
                        ),
                    )
                )
            ).all()
            if not entitlements:
                raise BillingDomainError("billing_entitlement_not_active")
            if len(entitlements) != 1:
                raise BillingDomainError("billing_entitlement_ambiguous")
            hard_limit = entitlements[0].hard_limit_microusd
            if hard_limit is None:
                raise BillingDomainError("billing_hard_limit_missing")

            held_expression = case(
                (
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.SETTLED.value,
                    BillingBudgetReservationModel.settled_microusd,
                ),
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
            held = await session.scalar(
                select(func.coalesce(func.sum(held_expression), 0)).where(
                    BillingBudgetReservationModel.workspace_id == workspace.id,
                    BillingBudgetReservationModel.product
                    == command.attribution.product.value,
                    BillingBudgetReservationModel.scope_kind
                    == command.attribution.scope_kind,
                    BillingBudgetReservationModel.scope_ref
                    == command.attribution.scope_ref,
                    BillingBudgetReservationModel.period_key == period_key,
                )
            )
            held_microusd = int(held or 0)
            if command.requested_microusd > hard_limit - held_microusd:
                return BudgetAdmission(
                    allowed=False,
                    reason_code="billing_hard_limit_exceeded",
                )

            row = BillingBudgetReservationModel(
                workspace_id=workspace.id,
                reservation_key=command.reservation_key,
                product=command.attribution.product.value,
                scope_kind=command.attribution.scope_kind,
                scope_ref=command.attribution.scope_ref,
                operation=command.operation.value,
                period_key=period_key,
                reserved_microusd=command.requested_microusd,
                settled_microusd=None,
                state=BudgetReservationState.RESERVED.value,
                error_code=None,
                created_at=command.occurred_at,
                updated_at=command.occurred_at,
            )
            session.add(row)
            await session.flush()
            return BudgetAdmission(
                allowed=True,
                reason_code="billing_reserved",
                reservation=_reservation(row, command),
            )


def _require_exact_replay(
    row: BillingBudgetReservationModel,
    command: ReserveBudgetCommand,
    *,
    period_key: str,
) -> None:
    expected = (
        command.attribution.product.value,
        command.attribution.scope_kind,
        command.attribution.scope_ref,
        command.operation.value,
        period_key,
        command.requested_microusd,
    )
    actual = (
        row.product,
        row.scope_kind,
        row.scope_ref,
        row.operation,
        row.period_key,
        row.reserved_microusd,
    )
    if actual != expected:
        raise BillingDomainError("billing_reservation_key_reused")


def _reservation(
    row: BillingBudgetReservationModel,
    command: ReserveBudgetCommand,
) -> BudgetReservation:
    return BudgetReservation(
        reservation_key=row.reservation_key,
        attribution=command.attribution,
        operation=command.operation,
        period_key=row.period_key,
        reserved_microusd=row.reserved_microusd,
        state=BudgetReservationState(row.state),
        created_at=row.created_at,
        updated_at=row.updated_at,
        settled_microusd=row.settled_microusd,
        error_code=row.error_code,
    )


def _period(value: str) -> None:
    if not value or value != value.strip() or len(value) > 64:
        raise BillingDomainError("billing_period_key_invalid")
