"""Transactional PostgreSQL hard-budget reservation adapter."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.billing import (
    BillingDomainError,
    BudgetAdmission,
    BudgetReservation,
    BudgetReservationState,
    EntitlementState,
    LedgerDirection,
    ReserveBudgetCommand,
    UsageUnit,
    mark_budget_unknown,
    release_budget,
    settle_budget,
)
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
    BillingLedgerEntryModel,
    BillingUsageEventModel,
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

    async def get_exact(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetReservation | None:
        _period(period_key)
        async with self._sessions() as session:
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.key == command.attribution.workspace_key,
                    Workspace.is_active.is_(True),
                )
            )
            if workspace is None:
                raise BillingDomainError("billing_workspace_not_active")
            row = await session.scalar(
                select(BillingBudgetReservationModel).where(
                    BillingBudgetReservationModel.workspace_id == workspace.id,
                    BillingBudgetReservationModel.reservation_key
                    == command.reservation_key,
                )
            )
            if row is None:
                return None
            _require_exact_replay(row, command, period_key=period_key)
            return _reservation(row, command)

    async def settle(
        self,
        reservation: BudgetReservation,
        *,
        actual_microusd: int,
        quantity: int,
        unit: UsageUnit,
        provider_operation_ref: str | None,
        occurred_at: datetime,
    ) -> None:
        overflow = False
        async with self._sessions() as session, session.begin():
            workspace, row = await _locked_reservation(session, reservation)
            if actual_microusd > row.reserved_microusd:
                row.state = BudgetReservationState.UNKNOWN.value
                row.error_code = "billing_settlement_exceeds_reservation"
                row.updated_at = occurred_at
                overflow = True
            else:
                settled = settle_budget(
                    reservation,
                    actual_microusd=actual_microusd,
                    occurred_at=occurred_at,
                )
                row.state = settled.state.value
                row.settled_microusd = settled.settled_microusd
                row.error_code = None
                row.updated_at = settled.updated_at
                operation_digest = _operation_digest(row.reservation_key)
                event_key = f"billing-usage:{operation_digest}"
                session.add(
                    BillingUsageEventModel(
                        workspace_id=workspace.id,
                        event_key=event_key,
                        product=row.product,
                        scope_kind=row.scope_kind,
                        scope_ref=row.scope_ref,
                        operation=row.operation,
                        quantity=quantity,
                        unit=unit.value,
                        cost_microusd=actual_microusd,
                        provider_operation_ref=provider_operation_ref,
                        occurred_at=occurred_at,
                    )
                )
                session.add(
                    BillingLedgerEntryModel(
                        workspace_id=workspace.id,
                        entry_key=f"billing-cost:{operation_digest}",
                        product=row.product,
                        scope_kind=row.scope_kind,
                        scope_ref=row.scope_ref,
                        direction=LedgerDirection.COST.value,
                        amount_microusd=actual_microusd,
                        currency="USD",
                        source_event_key=event_key,
                        occurred_at=occurred_at,
                    )
                )
        if overflow:
            raise BillingDomainError("billing_settlement_exceeds_reservation")

    async def release(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
    ) -> None:
        released = release_budget(reservation, occurred_at=occurred_at)
        async with self._sessions() as session, session.begin():
            _workspace, row = await _locked_reservation(session, reservation)
            row.state = released.state.value
            row.settled_microusd = None
            row.error_code = None
            row.updated_at = released.updated_at

    async def mark_unknown(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
        error_code: str,
    ) -> None:
        unknown = mark_budget_unknown(
            reservation,
            occurred_at=occurred_at,
            error_code=error_code,
        )
        async with self._sessions() as session, session.begin():
            _workspace, row = await _locked_reservation(session, reservation)
            row.state = unknown.state.value
            row.settled_microusd = None
            row.error_code = unknown.error_code
            row.updated_at = unknown.updated_at


async def _locked_reservation(
    session: AsyncSession,
    reservation: BudgetReservation,
) -> tuple[Workspace, BillingBudgetReservationModel]:
    workspace = await session.scalar(
        select(Workspace)
        .where(
            Workspace.key == reservation.attribution.workspace_key,
            Workspace.is_active.is_(True),
        )
        .with_for_update()
    )
    if workspace is None:
        raise BillingDomainError("billing_workspace_not_active")
    row = await session.scalar(
        select(BillingBudgetReservationModel)
        .where(
            BillingBudgetReservationModel.workspace_id == workspace.id,
            BillingBudgetReservationModel.reservation_key
            == reservation.reservation_key,
        )
        .with_for_update()
    )
    if row is None:
        raise BillingDomainError("billing_reservation_not_found")
    expected = (
        reservation.attribution.product.value,
        reservation.attribution.scope_kind,
        reservation.attribution.scope_ref,
        reservation.operation.value,
        reservation.period_key,
        reservation.reserved_microusd,
        BudgetReservationState.RESERVED.value,
    )
    actual = (
        row.product,
        row.scope_kind,
        row.scope_ref,
        row.operation,
        row.period_key,
        row.reserved_microusd,
        row.state,
    )
    if actual != expected:
        raise BillingDomainError("billing_reservation_binding_invalid")
    return workspace, row


def _operation_digest(reservation_key: str) -> str:
    return sha256(reservation_key.encode()).hexdigest()


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
