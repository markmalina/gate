"""Synthetic billing reconciliation blocks ambiguous provider admission."""

import pytest
from app.application.billing import (
    BillingDomainError,
    BillingProduct,
    BudgetReservationState,
)
from app.application.billing_reconciliation import (
    BillingReconciliationFact,
    evaluate_billing_incident,
    reconcile_billing_facts,
)


def fact(
    *,
    key: str = "operation-sensitive",
    state: BudgetReservationState = BudgetReservationState.SETTLED,
    settled: int | None = 1_000,
    usage: int | None = 1_000,
    ledger: int | None = 1_000,
    workspace: str = "mark",
) -> BillingReconciliationFact:
    return BillingReconciliationFact(
        operation_key=key,
        workspace_key=workspace,
        product=BillingProduct.ALPHA,
        scope_kind="installation",
        scope_ref="installation-sensitive",
        period_key="2026-07",
        reservation_state=state,
        reserved_microusd=2_000,
        settled_microusd=settled,
        usage_cost_microusd=usage,
        ledger_cost_microusd=ledger,
    )


def test_clean_reconciliation_allows_only_provider_admission() -> None:
    report = reconcile_billing_facts((fact(),))
    decision = evaluate_billing_incident(report)

    assert report.release_ready is True
    assert decision.automatic_stop is False
    assert decision.provider_admission_allowed is True
    assert decision.mutation_allowed is False
    assert decision.telegram_delivery_allowed is False


def test_unknown_reservation_triggers_automatic_stop() -> None:
    report = reconcile_billing_facts(
        (
            fact(
                state=BudgetReservationState.UNKNOWN,
                settled=None,
                usage=None,
                ledger=None,
            ),
        )
    )
    decision = evaluate_billing_incident(report)

    assert report.release_ready is False
    assert decision.automatic_stop is True
    assert decision.reason_codes == ("billing_reservations_unknown",)
    assert decision.provider_admission_allowed is False


def test_cost_mismatch_is_counted_without_mutation() -> None:
    report = reconcile_billing_facts((fact(ledger=900),))
    decision = evaluate_billing_incident(report)

    assert report.mismatch_count == 1
    assert report.discrepancy_microusd == 100
    assert decision.automatic_stop is True
    assert decision.mutation_allowed is False


def test_exact_duplicates_collapse_but_conflicts_block_release() -> None:
    original = fact()
    duplicate_report = reconcile_billing_facts((original, original))
    conflict_report = reconcile_billing_facts(
        (
            original,
            fact(ledger=900),
        )
    )

    assert duplicate_report.duplicate_count == 1
    assert duplicate_report.release_ready is True
    assert conflict_report.conflict_count == 1
    assert conflict_report.release_ready is False


def test_cross_workspace_reconciliation_fails_closed() -> None:
    with pytest.raises(
        BillingDomainError,
        match="billing_reconciliation_scope_mismatch",
    ):
        reconcile_billing_facts(
            (
                fact(),
                fact(key="operation-2", workspace="other"),
            )
        )


def test_report_exposes_only_digest_not_sensitive_operation_or_scope_ref() -> None:
    report = reconcile_billing_facts((fact(),))
    rendered = repr(report)

    assert len(report.evidence_sha256) == 64
    assert "operation-sensitive" not in rendered
    assert "installation-sensitive" not in rendered
