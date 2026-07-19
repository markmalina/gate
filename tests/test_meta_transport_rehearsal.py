"""Golden boundary tests for the dormant Meta transport rehearsal."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.meta_transport_rehearsal import (
    DeterministicFakeMetaProvider,
    MemoryMetaRehearsalReplayStore,
    MetaTransportRehearsalCommand,
    MetaTransportRehearsalError,
    RehearseMetaTransportService,
    SimulatedDeliveryOutcome,
)
from jsonschema import Draft202012Validator, FormatChecker

NOW = datetime(2026, 7, 19, 6, 0, tzinfo=UTC)


def _grant() -> dict[str, object]:
    return {
        "contract": "gate.delivery-grant",
        "version": "1.0.0",
        "event_id": "evt_portfolio_gate_001",
        "occurred_at": "2026-07-19T05:59:00Z",
        "idempotency_key": "idem_portfolio_gate_001",
        "correlation_id": "corr_portfolio_2026_001",
        "trace_id": "trace_portfolio_2026_001",
        "payload": {
            "delivery_intent_ref": "intent_portfolio_001",
            "approval_ref": "approval_simulated_001",
            "authorized": True,
            "expires_at": "2026-07-19T07:00:00Z",
        },
    }


def _command(**changes: object) -> MetaTransportRehearsalCommand:
    values: dict[str, object] = {
        "workspace_key": "workspace_portfolio_demo",
        "portfolio_run_id": "portfolio_run_2026_001",
        "grant": _grant(),
        "now": NOW,
        "enabled": True,
    }
    values.update(changes)
    return MetaTransportRehearsalCommand(**values)  # type: ignore[arg-type]


def _service(
    outcome: SimulatedDeliveryOutcome = SimulatedDeliveryOutcome.DELIVERED,
) -> tuple[RehearseMetaTransportService, DeterministicFakeMetaProvider]:
    provider = DeterministicFakeMetaProvider(outcome=outcome)
    return (
        RehearseMetaTransportService(
            provider=provider,
            replay_store=MemoryMetaRehearsalReplayStore(),
        ),
        provider,
    )


def test_golden_rehearsal_returns_canonical_delivery_fact() -> None:
    service, provider = _service()

    result = service.execute(_command())

    schema_path = (
        Path(__file__).parents[1]
        / "contracts/alphavia/v1/schemas/gate-delivery-fact.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        result.delivery_fact
    )
    assert result.delivery_fact["payload"]["outcome"] == "delivered"
    assert result.delivery_fact["payload"]["reconciliation_required"] is False
    assert result.external_delivery is False
    assert result.idempotent_replay is False
    assert provider.call_count == 1


def test_exact_duplicate_replays_same_fact_without_second_provider_call() -> None:
    service, provider = _service()

    first = service.execute(_command())
    replay = service.execute(_command())

    assert replay.delivery_fact == first.delivery_fact
    assert replay.idempotent_replay is True
    assert provider.call_count == 1


@pytest.mark.parametrize("field", ["workspace_key", "portfolio_run_id"])
def test_changed_binding_with_same_idempotency_key_fails_closed(field: str) -> None:
    service, provider = _service()
    service.execute(_command())

    with pytest.raises(MetaTransportRehearsalError, match="idempotency_conflict"):
        service.execute(_command(**{field: f"conflicting_{field}"}))

    assert provider.call_count == 1


def test_changed_grant_payload_with_same_idempotency_key_fails_closed() -> None:
    service, provider = _service()
    service.execute(_command())
    conflicting = deepcopy(_grant())
    assert isinstance(conflicting["payload"], dict)
    conflicting["payload"]["approval_ref"] = "approval_conflicting_001"

    with pytest.raises(MetaTransportRehearsalError, match="idempotency_conflict"):
        service.execute(_command(grant=conflicting))

    assert provider.call_count == 1


def test_disabled_rehearsal_performs_zero_provider_calls() -> None:
    service, provider = _service()

    with pytest.raises(MetaTransportRehearsalError, match="disabled"):
        service.execute(_command(enabled=False))

    assert provider.call_count == 0


@pytest.mark.parametrize("authorized", [False, None])
def test_rehearsal_never_self_authorizes_grant(authorized: object) -> None:
    service, provider = _service()
    grant = _grant()
    assert isinstance(grant["payload"], dict)
    grant["payload"]["authorized"] = authorized

    with pytest.raises(MetaTransportRehearsalError, match="not_authorized"):
        service.execute(_command(grant=grant))

    assert provider.call_count == 0


def test_expired_grant_fails_before_provider_call() -> None:
    service, provider = _service()
    grant = _grant()
    assert isinstance(grant["payload"], dict)
    grant["payload"]["expires_at"] = "2026-07-19T05:00:00Z"

    with pytest.raises(MetaTransportRehearsalError, match="expired"):
        service.execute(_command(grant=grant))

    assert provider.call_count == 0


def test_unknown_is_terminal_reconciliation_fact_without_retry_authority() -> None:
    service, provider = _service(SimulatedDeliveryOutcome.UNKNOWN)

    first = service.execute(_command())
    replay = service.execute(_command())

    assert first.delivery_fact["payload"]["outcome"] == "unknown"
    assert first.delivery_fact["payload"]["reconciliation_required"] is True
    assert "retry" not in first.delivery_fact["payload"]
    assert replay.delivery_fact == first.delivery_fact
    assert provider.call_count == 1


def test_slice_owns_no_product_or_person_authority() -> None:
    command_fields = set(MetaTransportRehearsalCommand.__dataclass_fields__)
    assert command_fields == {"workspace_key", "portfolio_run_id", "grant", "now", "enabled"}
    assert not command_fields & {
        "person",
        "campaign",
        "consent",
        "oko_session",
        "take_edit_plan",
        "provider_id",
        "raw_message",
    }
