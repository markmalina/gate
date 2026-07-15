"""Closed-state tests for Alpha model operational readiness."""

from dataclasses import replace
from typing import Any

from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    AlphaModelReadinessState,
    alpha_model_readiness_payload,
    evaluate_alpha_model_readiness,
)


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


def test_exact_prerequisites_wait_for_provider_preflight() -> None:
    readiness = evaluate_alpha_model_readiness(_facts())

    assert readiness.state is AlphaModelReadinessState.AWAITING_PROVIDER_PREFLIGHT
    assert readiness.prerequisites_ready is True
    assert readiness.ready_for_provider_preflight is True
    assert readiness.provider_preflight_verified is False


def test_configuration_deployment_and_budget_fail_closed_in_order() -> None:
    assert (
        evaluate_alpha_model_readiness(
            _facts(api_key_configured=False)
        ).state
        is AlphaModelReadinessState.CONFIGURATION_INCOMPLETE
    )
    assert (
        evaluate_alpha_model_readiness(
            _facts(staged_deployments=2)
        ).state
        is AlphaModelReadinessState.DEPLOYMENT_NOT_EXACT
    )
    assert (
        evaluate_alpha_model_readiness(
            _facts(reserved_microusd=50_000_000)
        ).state
        is AlphaModelReadinessState.BUDGET_EXHAUSTED
    )


def test_failed_preflight_never_claims_shadow_readiness() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(model_turns=1, failed_turns=1)
    )

    assert readiness.state is AlphaModelReadinessState.PROVIDER_PREFLIGHT_FAILED
    assert readiness.provider_preflight_verified is False
    assert readiness.ready_for_shadow_observation is False


def test_successful_provider_preflight_precedes_shadow_observation() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(provider_preflight_successes=1)
    )

    assert readiness.state is AlphaModelReadinessState.PROVIDER_PREFLIGHT_VERIFIED
    assert readiness.provider_preflight_verified is True
    assert readiness.ready_for_shadow_observation is True


def test_successful_delivery_free_turn_is_required_for_shadow_observation() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(model_turns=1, proposed_turns=1)
    )

    assert readiness.state is AlphaModelReadinessState.SHADOW_OBSERVED
    assert readiness.provider_preflight_verified is True
    assert readiness.ready_for_shadow_observation is True


def test_status_payload_is_redacted_and_reports_exact_budget() -> None:
    facts = replace(_facts(), reserved_microusd=12_288)
    payload = alpha_model_readiness_payload(facts)

    assert payload["state"] == "awaiting_provider_preflight"
    assert payload["telegram_delivery_blocked"] is True
    assert payload["provider_preflight_successes"] == 0
    assert payload["provider_preflight_failures"] == 0
    assert payload["budget"] == {
        "monthly_microusd": 50_000_000,
        "reserved_microusd": 12_288,
        "remaining_microusd": 49_987_712,
    }
    assert "api_key" not in payload
    assert "group_id" not in payload
