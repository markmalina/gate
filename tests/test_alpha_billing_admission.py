"""Alpha budget admission stays ordered, scoped and delivery-blocked."""

from datetime import UTC, datetime

import pytest
from app.application.alpha_access_policy import (
    AlphaAddressingSignal,
    AlphaDialogKind,
    AlphaInboundTurn,
    AlphaInstallationState,
)
from app.application.alpha_billing_admission import (
    AdmitAlphaBudgetedTurnCommand,
    AlphaBillingAdmissionService,
)
from app.application.billing import (
    BudgetAdmission,
    BudgetWindow,
    ReserveBudgetCommand,
    admit_budget,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


class FakeBudgetReservationPort:
    def __init__(self, *, limit_microusd: int) -> None:
        self.limit_microusd = limit_microusd
        self.commands: list[tuple[ReserveBudgetCommand, str]] = []

    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission:
        self.commands.append((command, period_key))
        return admit_budget(
            BudgetWindow(period_key, self.limit_microusd, 0, 0),
            command,
        )


def active_turn(*, connector_ready: bool = True) -> AlphaInboundTurn:
    return AlphaInboundTurn(
        dialog_kind=AlphaDialogKind.PRIVATE,
        installation_state=AlphaInstallationState.ACTIVE,
        signal=AlphaAddressingSignal.DIRECT_MESSAGE,
        sender_is_bot_or_service=False,
        connector_ready=connector_ready,
        provider_ready=True,
        billing_admitted=False,
    )


def command(
    *,
    turn: AlphaInboundTurn | None = None,
    requested_microusd: int = 10_000,
) -> AdmitAlphaBudgetedTurnCommand:
    return AdmitAlphaBudgetedTurnCommand(
        turn=turn or active_turn(),
        workspace_key="mark-personal",
        installation_ref="opaque-installation-sensitive",
        reservation_key="opaque-reservation-sensitive",
        period_key="2026-07",
        requested_microusd=requested_microusd,
        occurred_at=NOW,
    )


@pytest.mark.anyio
async def test_non_billing_denial_performs_no_budget_reservation() -> None:
    budget = FakeBudgetReservationPort(limit_microusd=50_000_000)
    service = AlphaBillingAdmissionService(budget)

    result = await service.admit(
        command(turn=active_turn(connector_ready=False))
    )

    assert result.turn.reason_code == "alpha_connector_not_ready"
    assert result.provider_call_allowed is False
    assert budget.commands == []


@pytest.mark.anyio
async def test_admitted_turn_reserves_alpha_installation_budget() -> None:
    budget = FakeBudgetReservationPort(limit_microusd=50_000_000)
    service = AlphaBillingAdmissionService(budget)

    result = await service.admit(command())

    assert result.turn.admitted is True
    assert result.provider_call_allowed is True
    assert result.telegram_delivery_allowed is False
    assert len(budget.commands) == 1
    reservation, period_key = budget.commands[0]
    assert reservation.attribution.product.value == "alpha"
    assert reservation.attribution.scope_kind == "installation"
    assert period_key == "2026-07"


@pytest.mark.anyio
async def test_hard_budget_denial_blocks_provider_call() -> None:
    budget = FakeBudgetReservationPort(limit_microusd=9_999)
    service = AlphaBillingAdmissionService(budget)

    result = await service.admit(command(requested_microusd=10_000))

    assert result.turn.reason_code == "alpha_billing_not_admitted"
    assert result.provider_call_allowed is False
    assert result.telegram_delivery_allowed is False


def test_sensitive_installation_and_reservation_refs_are_hidden() -> None:
    rendered = repr(command())

    assert "opaque-installation-sensitive" not in rendered
    assert "opaque-reservation-sensitive" not in rendered
