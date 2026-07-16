"""Provider-neutral billing, entitlement and hard-budget domain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum


class BillingDomainError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BillingProduct(StrEnum):
    GATE = "gate"
    ALPHA = "alpha"
    RELATIONSHIP_CRM = "relationship_crm"
    MAYAKI = "mayaki"
    OKO = "oko"


class UsageOperation(StrEnum):
    MODEL_RESPONSE = "model_response"
    TRANSCRIPTION = "transcription"
    TELEGRAM_DELIVERY = "telegram_delivery"
    INFRASTRUCTURE = "infrastructure"
    SUBSCRIPTION = "subscription"


class UsageUnit(StrEnum):
    TOKENS = "tokens"
    SECONDS = "seconds"
    ATTEMPTS = "attempts"
    EVENTS = "events"
    MONTHS = "months"


class LedgerDirection(StrEnum):
    COST = "cost"
    REVENUE = "revenue"
    CREDIT = "credit"
    ADJUSTMENT = "adjustment"


class EntitlementState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    EXPIRED = "expired"


class BudgetReservationState(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UsageAttribution:
    workspace_key: str
    product: BillingProduct
    scope_kind: str
    scope_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "billing_workspace_invalid", 128)
        _require_token(self.scope_kind, "billing_scope_kind_invalid", 64)
        _require_token(self.scope_ref, "billing_scope_ref_invalid", 255)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    event_key: str
    attribution: UsageAttribution = field(repr=False)
    operation: UsageOperation
    quantity: int
    unit: UsageUnit
    occurred_at: datetime
    cost_microusd: int | None = None
    provider_operation_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_token(self.event_key, "billing_event_key_invalid", 128)
        _require_aware(self.occurred_at, "billing_event_time_invalid")
        if self.quantity < 0:
            raise BillingDomainError("billing_quantity_invalid")
        if self.cost_microusd is not None and self.cost_microusd < 0:
            raise BillingDomainError("billing_cost_invalid")
        if self.provider_operation_ref is not None:
            _require_token(
                self.provider_operation_ref,
                "billing_provider_operation_ref_invalid",
                255,
            )


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_key: str
    attribution: UsageAttribution = field(repr=False)
    direction: LedgerDirection
    amount_microusd: int
    occurred_at: datetime
    source_event_key: str | None = None
    currency: str = "USD"

    def __post_init__(self) -> None:
        _require_token(self.entry_key, "billing_entry_key_invalid", 128)
        _require_aware(self.occurred_at, "billing_entry_time_invalid")
        if self.amount_microusd < 0:
            raise BillingDomainError("billing_amount_invalid")
        if self.currency != "USD":
            raise BillingDomainError("billing_currency_unsupported")
        if self.source_event_key is not None:
            _require_token(
                self.source_event_key,
                "billing_source_event_key_invalid",
                128,
            )


@dataclass(frozen=True, slots=True)
class Entitlement:
    entitlement_key: str
    attribution: UsageAttribution = field(repr=False)
    plan_key: str
    state: EntitlementState
    valid_from: datetime
    valid_until: datetime | None = None
    hard_limit_microusd: int | None = None

    def __post_init__(self) -> None:
        _require_token(
            self.entitlement_key,
            "billing_entitlement_key_invalid",
            128,
        )
        _require_token(self.plan_key, "billing_plan_key_invalid", 128)
        _require_aware(self.valid_from, "billing_entitlement_time_invalid")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "billing_entitlement_time_invalid")
            if self.valid_until <= self.valid_from:
                raise BillingDomainError("billing_entitlement_window_invalid")
        if self.hard_limit_microusd is not None and self.hard_limit_microusd < 0:
            raise BillingDomainError("billing_entitlement_limit_invalid")

    def allows(self, *, now: datetime) -> bool:
        _require_aware(now, "billing_entitlement_time_invalid")
        return (
            self.state is EntitlementState.ACTIVE
            and now >= self.valid_from
            and (self.valid_until is None or now < self.valid_until)
        )


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    period_key: str
    limit_microusd: int
    spent_microusd: int
    reserved_microusd: int

    def __post_init__(self) -> None:
        _require_token(self.period_key, "billing_period_key_invalid", 64)
        if min(
            self.limit_microusd,
            self.spent_microusd,
            self.reserved_microusd,
        ) < 0:
            raise BillingDomainError("billing_budget_value_invalid")
        if self.spent_microusd + self.reserved_microusd > self.limit_microusd:
            raise BillingDomainError("billing_budget_already_exceeded")

    @property
    def remaining_microusd(self) -> int:
        return self.limit_microusd - self.spent_microusd - self.reserved_microusd


@dataclass(frozen=True, slots=True)
class ReserveBudgetCommand:
    reservation_key: str
    attribution: UsageAttribution = field(repr=False)
    operation: UsageOperation
    requested_microusd: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_token(
            self.reservation_key,
            "billing_reservation_key_invalid",
            128,
        )
        _require_aware(self.occurred_at, "billing_reservation_time_invalid")
        if self.requested_microusd <= 0:
            raise BillingDomainError("billing_reservation_amount_invalid")


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_key: str
    attribution: UsageAttribution = field(repr=False)
    operation: UsageOperation
    period_key: str
    reserved_microusd: int
    state: BudgetReservationState
    created_at: datetime
    updated_at: datetime
    settled_microusd: int | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_token(
            self.reservation_key,
            "billing_reservation_key_invalid",
            128,
        )
        _require_token(self.period_key, "billing_period_key_invalid", 64)
        _require_aware(self.created_at, "billing_reservation_time_invalid")
        _require_aware(self.updated_at, "billing_reservation_time_invalid")
        if self.updated_at < self.created_at:
            raise BillingDomainError("billing_reservation_time_regression")
        if self.reserved_microusd <= 0:
            raise BillingDomainError("billing_reservation_amount_invalid")
        if self.settled_microusd is not None and (
            self.settled_microusd < 0
            or self.settled_microusd > self.reserved_microusd
        ):
            raise BillingDomainError("billing_settlement_amount_invalid")
        if self.error_code is not None:
            _require_token(self.error_code, "billing_error_code_invalid", 128)
        if self.state is BudgetReservationState.RESERVED and (
            self.settled_microusd is not None or self.error_code is not None
        ):
            raise BillingDomainError("billing_reserved_payload_invalid")
        if self.state is BudgetReservationState.SETTLED and (
            self.settled_microusd is None or self.error_code is not None
        ):
            raise BillingDomainError("billing_settled_payload_invalid")
        if self.state is BudgetReservationState.RELEASED and (
            self.settled_microusd is not None or self.error_code is not None
        ):
            raise BillingDomainError("billing_released_payload_invalid")
        if self.state is BudgetReservationState.UNKNOWN and self.error_code is None:
            raise BillingDomainError("billing_unknown_error_required")


@dataclass(frozen=True, slots=True)
class BudgetAdmission:
    allowed: bool
    reason_code: str
    reservation: BudgetReservation | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.allowed != (self.reservation is not None):
            raise BillingDomainError("billing_admission_inconsistent")


def admit_budget(
    window: BudgetWindow,
    command: ReserveBudgetCommand,
) -> BudgetAdmission:
    if command.requested_microusd > window.remaining_microusd:
        return BudgetAdmission(
            allowed=False,
            reason_code="billing_hard_limit_exceeded",
        )
    return BudgetAdmission(
        allowed=True,
        reason_code="billing_reserved",
        reservation=BudgetReservation(
            reservation_key=command.reservation_key,
            attribution=command.attribution,
            operation=command.operation,
            period_key=window.period_key,
            reserved_microusd=command.requested_microusd,
            state=BudgetReservationState.RESERVED,
            created_at=command.occurred_at,
            updated_at=command.occurred_at,
        ),
    )


def settle_budget(
    reservation: BudgetReservation,
    *,
    actual_microusd: int,
    occurred_at: datetime,
) -> BudgetReservation:
    _require_reserved(reservation)
    return replace(
        reservation,
        state=BudgetReservationState.SETTLED,
        settled_microusd=actual_microusd,
        updated_at=occurred_at,
    )


def release_budget(
    reservation: BudgetReservation,
    *,
    occurred_at: datetime,
) -> BudgetReservation:
    _require_reserved(reservation)
    return replace(
        reservation,
        state=BudgetReservationState.RELEASED,
        updated_at=occurred_at,
    )


def mark_budget_unknown(
    reservation: BudgetReservation,
    *,
    occurred_at: datetime,
    error_code: str,
) -> BudgetReservation:
    _require_reserved(reservation)
    return replace(
        reservation,
        state=BudgetReservationState.UNKNOWN,
        updated_at=occurred_at,
        error_code=error_code,
    )


def _require_reserved(reservation: BudgetReservation) -> None:
    if reservation.state is not BudgetReservationState.RESERVED:
        raise BillingDomainError("billing_reservation_not_open")


def _require_token(value: str, code: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise BillingDomainError(code)


def _require_aware(value: datetime, code: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BillingDomainError(code)
