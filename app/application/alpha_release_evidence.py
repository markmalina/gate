"""Content-free release evidence for Alpha's pre-billing model path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.application.alpha_model_adversarial_evals import (
    evaluate_alpha_adversarial_suite,
)
from app.application.alpha_model_product_evals import evaluate_alpha_product_suite
from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    alpha_model_readiness_payload,
)

EVIDENCE_SCHEMA_VERSION = "alpha-release-evidence-v1"


@dataclass(frozen=True, slots=True)
class AlphaReleaseEvidence:
    """Deterministic evidence assembled without provider or Telegram access."""

    payload: dict[str, Any]
    sha256: str


def build_alpha_release_evidence(
    facts: AlphaModelReadinessFacts,
) -> AlphaReleaseEvidence:
    """Compose offline suites and release gates into one immutable report."""

    product = evaluate_alpha_product_suite()
    adversarial = evaluate_alpha_adversarial_suite()
    readiness = alpha_model_readiness_payload(facts)
    promotion = readiness["promotion_dry_run"]
    if not isinstance(promotion, dict):
        raise ValueError("alpha_release_evidence_promotion_invalid")

    payload: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "execution_mode": "offline_evidence",
        "network_calls_allowed": False,
        "mutation_allowed": False,
        "telegram_delivery_blocked": facts.telegram_delivery_blocked,
        "fixture_binding_active": facts.fixture_binding_active,
        "rollback_target": promotion["rollback_target"],
        "readiness": {
            "state": readiness["state"],
            "provider_preflight_verified": readiness[
                "provider_preflight_verified"
            ],
            "ready_for_provider_preflight": readiness[
                "ready_for_provider_preflight"
            ],
            "ready_for_shadow_observation": readiness[
                "ready_for_shadow_observation"
            ],
            "alerts": readiness["alerts"],
            "blocked_by": promotion["blocked_by"],
        },
        "offline_suites": {
            "product": {
                "passed": product.passed,
                "cases": product.total_cases,
                "russian_cases": product.russian_cases,
                "english_cases": product.english_cases,
            },
            "adversarial": {
                "passed": adversarial.passed,
                "cases": adversarial.total_cases,
                "prompt_injection_cases": adversarial.prompt_injection_cases,
                "isolation_cases": adversarial.isolation_cases,
                "handoff_cases": adversarial.handoff_cases,
            },
        },
        "shadow_sample": {
            "observed": facts.proposed_turns,
            "required": promotion["shadow_proposals_required"],
            "limited_canary_review_ready": promotion[
                "limited_canary_review_ready"
            ],
        },
        "budget": readiness["budget"],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return AlphaReleaseEvidence(
        payload=payload,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def alpha_release_evidence_payload(
    facts: AlphaModelReadinessFacts,
) -> dict[str, Any]:
    """Return a JSON-ready report with a digest over all evidence fields."""

    evidence = build_alpha_release_evidence(facts)
    return {**evidence.payload, "evidence_sha256": evidence.sha256}
