"""Content-free synthetic billing reconciliation and automatic-stop evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256

from app.application.billing import (
    BillingDomainError,
    BillingProduct,
    BudgetReservationState,
)


class BillingReconciliationState(StrEnum):
    MATCHED = "matched"
    OPEN = "open"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class BillingReconciliationFact:
    operation_key: str = field(repr=False)
    workspace_key: str
    product: BillingProduct
    scope_kind: str
    scope_ref: str = field(repr=False)
    period_key: str
    reservation_state: BudgetReservationState
    reserved_microusd: int
    settled_microusd: int | None
    usage_cost_microusd: int | None
    ledger_cost_microusd: int | None

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.operation_key, "billing_reconciliation_operation", 128),
            (self.workspace_key, "billing_workspace_invalid", 128),
            (self.scope_kind, "billing_scope_kind_invalid", 64),
            (self.scope_ref, "billing_scope_ref_invalid", 255),
            (self.period_key, "billing_period_key_invalid", 64),
        ):
            if not value or value != value.strip() or len(value) > maximum:
                raise BillingDomainError(name)
        values = (
            self.reserved_microusd,
            self.settled_microusd,
            self.usage_cost_microusd,
            self.ledger_cost_microusd,
        )
        if self.reserved_microusd <= 0 or any(
            value is not None and value < 0 for value in values
        ):
            raise BillingDomainError("billing_reconciliation_amount_invalid")


@dataclass(frozen=True, slots=True)
class BillingReconciliationReport:
    workspace_key: str
    product: BillingProduct
    scope_kind: str
    period_key: str
    input_count: int
    matched_count: int
    open_count: int
    unknown_count: int
    mismatch_count: int
    duplicate_count: int
    conflict_count: int
    discrepancy_microusd: int
    evidence_sha256: str
    mutation_allowed: bool = False
    telegram_delivery_allowed: bool = False

    @property
    def release_ready(self) -> bool:
        return (
            self.input_count > 0
            and self.open_count == 0
            and self.unknown_count == 0
            and self.mismatch_count == 0
            and self.conflict_count == 0
            and self.discrepancy_microusd == 0
        )


@dataclass(frozen=True, slots=True)
class BillingIncidentDecision:
    automatic_stop: bool
    reason_codes: tuple[str, ...]
    provider_admission_allowed: bool
    mutation_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if self.provider_admission_allowed == self.automatic_stop:
            raise BillingDomainError("billing_incident_decision_inconsistent")
        if self.mutation_allowed or self.telegram_delivery_allowed:
            raise BillingDomainError("billing_incident_side_effect_forbidden")


def reconcile_billing_facts(
    facts: tuple[BillingReconciliationFact, ...],
) -> BillingReconciliationReport:
    if not facts:
        raise BillingDomainError("billing_reconciliation_empty")
    first = facts[0]
    scope = (
        first.workspace_key,
        first.product,
        first.scope_kind,
        first.scope_ref,
        first.period_key,
    )
    if any(
        (
            fact.workspace_key,
            fact.product,
            fact.scope_kind,
            fact.scope_ref,
            fact.period_key,
        )
        != scope
        for fact in facts
    ):
        raise BillingDomainError("billing_reconciliation_scope_mismatch")

    unique: dict[str, BillingReconciliationFact] = {}
    duplicates = 0
    conflicts = 0
    for fact in facts:
        previous = unique.get(fact.operation_key)
        if previous is None:
            unique[fact.operation_key] = fact
        elif previous == fact:
            duplicates += 1
        else:
            conflicts += 1

    classified = tuple(
        (key, _classify(fact))
        for key, fact in sorted(unique.items(), key=lambda item: item[0])
    )
    discrepancy = sum(_discrepancy(unique[key]) for key, _ in classified)
    material = "\n".join(f"{key}|{state.value}" for key, state in classified)
    return BillingReconciliationReport(
        workspace_key=first.workspace_key,
        product=first.product,
        scope_kind=first.scope_kind,
        period_key=first.period_key,
        input_count=len(facts),
        matched_count=sum(
            state is BillingReconciliationState.MATCHED for _, state in classified
        ),
        open_count=sum(
            state is BillingReconciliationState.OPEN for _, state in classified
        ),
        unknown_count=sum(
            state is BillingReconciliationState.UNKNOWN for _, state in classified
        ),
        mismatch_count=sum(
            state is BillingReconciliationState.MISMATCH for _, state in classified
        ),
        duplicate_count=duplicates,
        conflict_count=conflicts,
        discrepancy_microusd=discrepancy,
        evidence_sha256=sha256(material.encode()).hexdigest(),
    )


def evaluate_billing_incident(
    report: BillingReconciliationReport,
) -> BillingIncidentDecision:
    reasons: list[str] = []
    if report.open_count:
        reasons.append("billing_reservations_open")
    if report.unknown_count:
        reasons.append("billing_reservations_unknown")
    if report.mismatch_count or report.discrepancy_microusd:
        reasons.append("billing_reconciliation_mismatch")
    if report.conflict_count:
        reasons.append("billing_reconciliation_conflict")
    if not report.input_count:
        reasons.append("billing_reconciliation_missing")
    stopped = bool(reasons)
    return BillingIncidentDecision(
        automatic_stop=stopped,
        reason_codes=tuple(reasons) or ("billing_reconciliation_clean",),
        provider_admission_allowed=not stopped,
    )


def _classify(fact: BillingReconciliationFact) -> BillingReconciliationState:
    if fact.reservation_state is BudgetReservationState.RESERVED:
        return BillingReconciliationState.OPEN
    if fact.reservation_state is BudgetReservationState.UNKNOWN:
        return BillingReconciliationState.UNKNOWN
    if fact.reservation_state is BudgetReservationState.RELEASED:
        if any(
            value not in {None, 0}
            for value in (
                fact.settled_microusd,
                fact.usage_cost_microusd,
                fact.ledger_cost_microusd,
            )
        ):
            return BillingReconciliationState.MISMATCH
        return BillingReconciliationState.MATCHED
    amounts = (
        fact.settled_microusd,
        fact.usage_cost_microusd,
        fact.ledger_cost_microusd,
    )
    if None in amounts or len(set(amounts)) != 1:
        return BillingReconciliationState.MISMATCH
    return BillingReconciliationState.MATCHED


def _discrepancy(fact: BillingReconciliationFact) -> int:
    amounts = tuple(
        value
        for value in (
            fact.settled_microusd,
            fact.usage_cost_microusd,
            fact.ledger_cost_microusd,
        )
        if value is not None
    )
    return max(amounts, default=0) - min(amounts, default=0)
