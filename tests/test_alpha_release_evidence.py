"""Composed pre-billing release evidence for Alpha."""

from typing import Any

from app.application.alpha_model_readiness import AlphaModelReadinessFacts
from app.application.alpha_release_evidence import alpha_release_evidence_payload


def _facts(**changes: Any) -> AlphaModelReadinessFacts:
    values: dict[str, Any] = {
        "runtime_enabled": True,
        "data_gate_approved": True,
        "api_key_configured": True,
        "staged_deployments": 1,
        "monthly_budget_microusd": 50_000_000,
        "reserved_microusd": 0,
        "model_turns": 0,
        "proposed_turns": 0,
        "failed_turns": 0,
    }
    values.update(changes)
    return AlphaModelReadinessFacts(**values)


def test_prebilling_evidence_is_content_free_and_fail_closed() -> None:
    report = alpha_release_evidence_payload(_facts())

    assert report["schema_version"] == "alpha-release-evidence-v1"
    assert report["execution_mode"] == "offline_evidence"
    assert report["network_calls_allowed"] is False
    assert report["mutation_allowed"] is False
    assert report["telegram_delivery_blocked"] is True
    assert report["rollback_target"] == "fixture_canary"
    assert report["readiness"]["state"] == "awaiting_provider_preflight"
    assert report["offline_suites"]["product"] == {
        "passed": True,
        "cases": 12,
        "russian_cases": 11,
        "english_cases": 1,
    }
    assert report["offline_suites"]["adversarial"]["passed"] is True
    assert len(report["evidence_sha256"]) == 64
    serialized = str(report).casefold()
    assert "api_key" not in serialized
    assert "group_id" not in serialized
    assert "/alpha" not in serialized


def test_evidence_digest_is_stable_and_changes_with_release_facts() -> None:
    first = alpha_release_evidence_payload(_facts())
    replay = alpha_release_evidence_payload(_facts())
    changed = alpha_release_evidence_payload(
        _facts(provider_preflight_successes=1)
    )

    assert first == replay
    assert first["evidence_sha256"] != changed["evidence_sha256"]


def test_any_shadow_delivery_grant_blocks_release_evidence() -> None:
    report = alpha_release_evidence_payload(
        _facts(provider_preflight_successes=1, model_delivery_grants=1)
    )

    assert report["readiness"]["state"] == "safety_invariant_violated"
    assert report["readiness"]["ready_for_shadow_observation"] is False
    assert report["shadow_sample"]["limited_canary_review_ready"] is False
    assert report["mutation_allowed"] is False
