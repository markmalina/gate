"""Budget admission coordinator for Alpha turns, without provider or delivery I/O."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

from app.application.alpha_access_policy import (
    AlphaInboundTurn,
    AlphaTurnDecision,
    evaluate_inbound_turn,
)
from app.application.billing import (
    BillingProduct,
    BudgetAdmission,
    ReserveBudgetCommand,
    UsageAttribution,
    UsageOperation,
)


@dataclass(frozen=True, slots=True)
class AdmitAlphaBudgetedTurnCommand:
    turn: AlphaInboundTurn
    workspace_key: str
    installation_ref: str = field(repr=False)
    reservation_key: str = field(repr=False)
    period_key: str
    requested_microusd: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AlphaBudgetedTurnAdmission:
    turn: AlphaTurnDecision
    budget: BudgetAdmission | None = field(default=None, repr=False)
    provider_call_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        budget_allowed = self.budget is not None and self.budget.allowed
        if self.provider_call_allowed != (self.turn.admitted and budget_allowed):
            raise ValueError("alpha_budget_admission_inconsistent")
        if self.telegram_delivery_allowed:
            raise ValueError("alpha_budget_admission_cannot_grant_delivery")


class AlphaBudgetReservationPort(Protocol):
    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission: ...


class AlphaBillingAdmissionService:
    """Reserve cost only after every non-billing Alpha gate has passed."""

    def __init__(self, budget: AlphaBudgetReservationPort) -> None:
        self._budget = budget

    async def admit(
        self,
        command: AdmitAlphaBudgetedTurnCommand,
    ) -> AlphaBudgetedTurnAdmission:
        non_billing = evaluate_inbound_turn(
            replace(command.turn, billing_admitted=True)
        )
        if not non_billing.admitted:
            return AlphaBudgetedTurnAdmission(turn=non_billing)

        budget = await self._budget.reserve(
            ReserveBudgetCommand(
                reservation_key=command.reservation_key,
                attribution=UsageAttribution(
                    workspace_key=command.workspace_key,
                    product=BillingProduct.ALPHA,
                    scope_kind="installation",
                    scope_ref=command.installation_ref,
                ),
                operation=UsageOperation.MODEL_RESPONSE,
                requested_microusd=command.requested_microusd,
                occurred_at=command.occurred_at,
            ),
            period_key=command.period_key,
        )
        decision = evaluate_inbound_turn(
            replace(command.turn, billing_admitted=budget.allowed)
        )
        return AlphaBudgetedTurnAdmission(
            turn=decision,
            budget=budget,
            provider_call_allowed=decision.admitted and budget.allowed,
        )
