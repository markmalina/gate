"""Contracts for provider-neutral billing, entitlements and hard budgets."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.billing import (
    BillingDomainError,
    BillingProduct,
    BudgetReservationState,
    BudgetWindow,
    Entitlement,
    EntitlementState,
    LedgerDirection,
    LedgerEntry,
    ReserveBudgetCommand,
    UsageAttribution,
    UsageEvent,
    UsageOperation,
    UsageUnit,
    admit_budget,
    mark_budget_unknown,
    release_budget,
    settle_budget,
)

NOW = datetime(2026, 7, 16, 21, 0, tzinfo=UTC)


def _scope(product: BillingProduct = BillingProduct.ALPHA) -> UsageAttribution:
    return UsageAttribution(
        workspace_key="mark-personal",
        product=product,
        scope_kind="installation",
        scope_ref="opaque-installation-ref",
    )


def _reserve(amount: int) -> ReserveBudgetCommand:
    return ReserveBudgetCommand(
        reservation_key=f"reservation-{amount}",
        attribution=_scope(),
        operation=UsageOperation.MODEL_RESPONSE,
        requested_microusd=amount,
        occurred_at=NOW,
    )


def test_every_product_uses_the_same_attribution_contract() -> None:
    assert {product.value for product in BillingProduct} == {
        "gate",
        "alpha",
        "relationship_crm",
        "mayaki",
        "oko",
    }
    assert _scope(BillingProduct.RELATIONSHIP_CRM).product is BillingProduct.RELATIONSHIP_CRM


def test_hard_budget_admits_exact_remaining_and_blocks_one_more() -> None:
    window = BudgetWindow(
        period_key="2026-07",
        limit_microusd=50_000_000,
        spent_microusd=10_000_000,
        reserved_microusd=39_000_000,
    )

    exact = admit_budget(window, _reserve(1_000_000))
    blocked = admit_budget(window, _reserve(1_000_001))

    assert exact.allowed is True
    assert exact.reservation is not None
    assert exact.reservation.state is BudgetReservationState.RESERVED
    assert blocked.allowed is False
    assert blocked.reason_code == "billing_hard_limit_exceeded"
    assert blocked.reservation is None


def test_reservation_has_closed_settle_release_and_unknown_outcomes() -> None:
    window = BudgetWindow(
        period_key="2026-07",
        limit_microusd=50_000_000,
        spent_microusd=0,
        reserved_microusd=0,
    )
    reserved = admit_budget(window, _reserve(2_000_000)).reservation
    assert reserved is not None

    settled = settle_budget(
        reserved,
        actual_microusd=1_500_000,
        occurred_at=NOW + timedelta(seconds=1),
    )
    released = release_budget(
        reserved,
        occurred_at=NOW + timedelta(seconds=1),
    )
    unknown = mark_budget_unknown(
        reserved,
        occurred_at=NOW + timedelta(seconds=1),
        error_code="provider_cost_ambiguous",
    )

    assert settled.state is BudgetReservationState.SETTLED
    assert settled.settled_microusd == 1_500_000
    assert released.state is BudgetReservationState.RELEASED
    assert unknown.state is BudgetReservationState.UNKNOWN
    with pytest.raises(BillingDomainError, match="billing_reservation_not_open"):
        release_budget(settled, occurred_at=NOW + timedelta(seconds=2))


def test_actual_cost_cannot_exceed_reserved_amount() -> None:
    reserved = admit_budget(
        BudgetWindow("2026-07", 50_000_000, 0, 0),
        _reserve(1_000_000),
    ).reservation
    assert reserved is not None

    with pytest.raises(BillingDomainError, match="billing_settlement_amount_invalid"):
        settle_budget(
            reserved,
            actual_microusd=1_000_001,
            occurred_at=NOW + timedelta(seconds=1),
        )


def test_cost_and_revenue_are_separate_immutable_entries() -> None:
    usage = UsageEvent(
        event_key="usage-transcription-1",
        attribution=_scope(),
        operation=UsageOperation.TRANSCRIPTION,
        quantity=42,
        unit=UsageUnit.SECONDS,
        occurred_at=NOW,
        cost_microusd=2_500,
        provider_operation_ref="provider-op-sensitive",
    )
    cost = LedgerEntry(
        entry_key="cost-1",
        attribution=usage.attribution,
        direction=LedgerDirection.COST,
        amount_microusd=2_500,
        occurred_at=NOW,
        source_event_key=usage.event_key,
    )
    revenue = LedgerEntry(
        entry_key="revenue-1",
        attribution=usage.attribution,
        direction=LedgerDirection.REVENUE,
        amount_microusd=10_000,
        occurred_at=NOW,
        source_event_key=usage.event_key,
    )

    assert cost.direction is LedgerDirection.COST
    assert revenue.direction is LedgerDirection.REVENUE
    assert revenue.amount_microusd - cost.amount_microusd == 7_500
    assert "provider-op-sensitive" not in repr(usage)


def test_entitlement_is_default_inactive_outside_its_window() -> None:
    entitlement = Entitlement(
        entitlement_key="alpha-plan-1",
        attribution=_scope(),
        plan_key="alpha-test",
        state=EntitlementState.ACTIVE,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        hard_limit_microusd=50_000_000,
    )

    assert entitlement.allows(now=NOW) is True
    assert entitlement.allows(now=NOW - timedelta(seconds=1)) is False
    assert entitlement.allows(now=NOW + timedelta(days=30)) is False
