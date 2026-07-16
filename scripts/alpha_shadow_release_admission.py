"""Build content-free prebilling and post-preflight admission evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from app.application.alpha_shadow_release_admission import (
    AlphaShadowAdmissionFacts,
    alpha_shadow_admission_payload,
    evaluate_alpha_shadow_admission,
)


def _baseline() -> AlphaShadowAdmissionFacts:
    return AlphaShadowAdmissionFacts(
        provider_preflight_verified=False,
        provider_preflight_failures=0,
        staged_deployments=1,
        approved_installations=1,
        deployment_key="alpha-model-v1-openai",
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        fixture_binding_active=True,
        telegram_delivery_blocked=True,
        model_delivery_grants=0,
        stuck_provider_preflights=0,
        failed_model_turns=0,
        budget_remaining_microusd=50_000_000,
        release_evidence_passed=True,
        incident_evidence_passed=True,
        retention_evidence_passed=True,
        restore_evidence_passed=True,
        supply_chain_evidence_passed=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline = _baseline()
    report = {
        "schema_version": "alpha-shadow-release-plan-v1",
        "prebilling": alpha_shadow_admission_payload(
            evaluate_alpha_shadow_admission(baseline)
        ),
        "post_preflight": alpha_shadow_admission_payload(
            evaluate_alpha_shadow_admission(
                replace(baseline, provider_preflight_verified=True)
            )
        ),
    }
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
