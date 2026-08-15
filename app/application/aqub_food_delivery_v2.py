"""Disabled-by-default food-specific acceptance seam for AQUB contract v2."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.application.aqub_contract_v2 import (
    AQUBContractV2Error,
    assert_route_match_v2,
    map_opaque_binding_evidence_v2,
    parse_delivery_ack_v2,
    parse_delivery_command_v2,
)


class AQUBFoodDeliveryV2Mode(StrEnum):
    DISABLED = "disabled"
    SYNTHETIC_REHEARSAL = "synthetic_rehearsal"


@dataclass(frozen=True, slots=True)
class AQUBFoodDeliveryV2Adapter:
    mode: AQUBFoodDeliveryV2Mode = AQUBFoodDeliveryV2Mode.DISABLED

    def __post_init__(self) -> None:
        if type(self.mode) is not AQUBFoodDeliveryV2Mode:
            raise AQUBContractV2Error("AQUB_FOOD_DELIVERY_MODE_INVALID")

    def rehearse_acceptance(
        self,
        command: Mapping[str, Any],
        binding_evidence: Mapping[str, Any],
        *,
        observed_at: str,
        duplicate: bool = False,
    ) -> dict[str, Any]:
        if self.mode is not AQUBFoodDeliveryV2Mode.SYNTHETIC_REHEARSAL:
            raise AQUBContractV2Error("AQUB_FOOD_DELIVERY_DISABLED")

        document = parse_delivery_command_v2(command)
        wire = document.to_wire()
        if not wire["idempotency_key"].startswith("food-reply/"):
            raise AQUBContractV2Error("AQUB_FOOD_DELIVERY_SCOPE_REQUIRED")
        route = map_opaque_binding_evidence_v2(binding_evidence)
        assert_route_match_v2(document, route)

        digest = hashlib.sha256(
            wire["idempotency_key"].encode("utf-8")
        ).hexdigest()
        ack = {
            "schema_version": "aqub.delivery-ack.v2",
            "ack_ref": f"ack:{digest}",
            "command_id": wire["command_id"],
            "idempotency_key": wire["idempotency_key"],
            "installation_ref": wire["installation_ref"],
            "cube_ref": wire["cube_ref"],
            "route_generation": wire["route_generation"],
            "observed_at": observed_at,
            "status": "duplicate" if duplicate else "committed",
        }
        return parse_delivery_ack_v2(ack).to_wire()
