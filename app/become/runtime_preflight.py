"""Fail-closed preflight for packaging the BECOME transport proof modules."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from importlib import import_module
from types import ModuleType

MODULES: tuple[tuple[str, str], ...] = (
    ("src.become_w1.transport_store", "GateTransportStore"),
    ("src.become_w2.entry_adapter", "GateEntryAdapter"),
    ("src.become_w3.voice_text_intake", "VoiceTextIntakeStore"),
    ("src.become_w5.controlled_effects", "ControlledEffectStore"),
    ("src.become_w6.learner_delivery", "LearnerDeliveryStore"),
    ("src.become_w7.stars_test_order", "StarsTestOrderStore"),
    ("src.become_w8.referral_delivery", "ReferralDeliveryStore"),
    ("src.become_w9.chaos_recovery", "ChaosStore"),
)

LIVE_FLAGS = (
    "GATE_BECOME_LIVE_ENABLED",
    "GATE_BECOME_LIVE_PAYMENT_ENABLED",
    "GATE_BECOME_PUBLICATION_ENABLED",
    "GATE_BECOME_SEND_ENABLED",
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_runtime_preflight(
    *,
    importer: Callable[[str], ModuleType] = import_module,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, object]:
    """Verify packaged modules without constructing stores or contacting providers."""

    enabled_live_flags = [name for name in LIVE_FLAGS if _truthy(environ.get(name))]
    if enabled_live_flags:
        raise RuntimeError("become_live_flags_must_remain_disabled")

    checks: list[dict[str, object]] = []
    for module_name, symbol_name in MODULES:
        module = importer(module_name)
        symbol = getattr(module, symbol_name, None)
        if symbol is None:
            raise RuntimeError(f"become_runtime_symbol_missing:{module_name}:{symbol_name}")
        checks.append(
            {
                "module": module_name,
                "symbol": symbol_name,
                "importable": True,
            }
        )

    return {
        "schema": "gate.become-runtime-preflight@1",
        "phase": "I1",
        "modules": checks,
        "module_count": len(checks),
        "provider_execution_enabled": False,
        "allowed_modes": ["SIMULATED"],
        "disabled_modes": ["TEST", "CONTROLLED", "LIVE"],
        "external_effects": 0,
        "production_ready": False,
    }


def main() -> None:
    print(json.dumps(build_runtime_preflight(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
