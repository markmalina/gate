from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.application.aqub_contract_v2 import AQUBContractV2Error
from app.application.aqub_food_delivery_v2 import (
    AQUBFoodDeliveryV2Adapter,
    AQUBFoodDeliveryV2Mode,
)

FIXTURE = (
    Path(__file__).parents[1]
    / "contracts"
    / "aqub"
    / "v2"
    / "fixtures"
    / "delivery-command.food-reply.json"
)


def _command() -> dict[str, object]:
    return json.loads(FIXTURE.read_text())


def _evidence(command: dict[str, object]) -> dict[str, object]:
    return {
        "binding_ref": "binding:opaque_binding_01",
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
    }


def test_food_acceptance_is_disabled_by_default() -> None:
    command = _command()
    with pytest.raises(AQUBContractV2Error, match="AQUB_FOOD_DELIVERY_DISABLED"):
        AQUBFoodDeliveryV2Adapter().rehearse_acceptance(
            command,
            _evidence(command),
            observed_at="2026-08-10T12:00:02.000Z",
        )


def test_synthetic_acceptance_returns_correlated_ack() -> None:
    command = _command()
    adapter = AQUBFoodDeliveryV2Adapter(
        mode=AQUBFoodDeliveryV2Mode.SYNTHETIC_REHEARSAL
    )
    ack = adapter.rehearse_acceptance(
        command,
        _evidence(command),
        observed_at="2026-08-10T12:00:02.000Z",
    )
    assert ack["status"] == "committed"
    for key in (
        "command_id",
        "idempotency_key",
        "installation_ref",
        "cube_ref",
        "route_generation",
    ):
        assert ack[key] == command[key]


def test_duplicate_is_explicit_and_never_sends() -> None:
    command = _command()
    adapter = AQUBFoodDeliveryV2Adapter(
        mode=AQUBFoodDeliveryV2Mode.SYNTHETIC_REHEARSAL
    )
    ack = adapter.rehearse_acceptance(
        command,
        _evidence(command),
        observed_at="2026-08-10T12:00:02.000Z",
        duplicate=True,
    )
    assert ack["status"] == "duplicate"


def test_generic_program_command_cannot_enter_food_seam() -> None:
    command = _command()
    command["idempotency_key"] = "delivery/program/0001"
    adapter = AQUBFoodDeliveryV2Adapter(
        mode=AQUBFoodDeliveryV2Mode.SYNTHETIC_REHEARSAL
    )
    with pytest.raises(AQUBContractV2Error, match="AQUB_FOOD_DELIVERY_SCOPE_REQUIRED"):
        adapter.rehearse_acceptance(
            command,
            _evidence(command),
            observed_at="2026-08-10T12:00:02.000Z",
        )


def test_stale_route_generation_fails_closed() -> None:
    command = _command()
    evidence = _evidence(command)
    evidence["route_generation"] = 2
    adapter = AQUBFoodDeliveryV2Adapter(
        mode=AQUBFoodDeliveryV2Mode.SYNTHETIC_REHEARSAL
    )
    with pytest.raises(AQUBContractV2Error, match="ROUTE_MISMATCH"):
        adapter.rehearse_acceptance(
            command,
            evidence,
            observed_at="2026-08-10T12:00:02.000Z",
        )
