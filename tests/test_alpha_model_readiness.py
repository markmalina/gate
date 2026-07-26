"""Closed-state tests for Alpha model operational readiness."""

from dataclasses import replace
from typing import Any

import pytest
from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    AlphaModelReadinessState,
    alpha_model_readiness_payload,
    alpha_release_binding_readiness,
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


def test_model_delivery_grant_is_a_critical_fail_closed_invariant() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(provider_preflight_successes=1, model_delivery_grants=1)
    )

    assert readiness.state is AlphaModelReadinessState.SAFETY_INVARIANT_VIOLATED
    assert readiness.prerequisites_ready is False
    assert readiness.ready_for_shadow_observation is False
    assert readiness.alerts == ("critical:model_delivery_grant_detected",)


def test_stale_reserved_preflight_blocks_progress() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(stuck_provider_preflights=1)
    )

    assert readiness.state is AlphaModelReadinessState.PROVIDER_PREFLIGHT_STUCK
    assert readiness.ready_for_provider_preflight is False
    assert readiness.alerts == ("critical:provider_preflight_stuck",)


def test_provider_errors_are_redacted_to_bounded_categories() -> None:
    payload = alpha_model_readiness_payload(
        _facts(
            provider_preflight_failures=1,
            last_provider_error_code="openai_credentials_rejected",
        )
    )

    assert payload["last_provider_error_category"] == "credentials"
    assert payload["alerts"] == [
        "warning:provider_preflight_failure:credentials"
    ]
    assert "openai_credentials_rejected" not in str(payload)


def test_promotion_plan_is_dry_run_and_never_mutates_release_state() -> None:
    payload = alpha_model_readiness_payload(_facts())
    plan = payload["promotion_dry_run"]

    assert isinstance(plan, dict)
    assert plan["execution_mode"] == "dry_run"
    assert plan["mutation_allowed"] is False
    assert plan["current_stage"] == "fixture_canary"
    assert plan["next_stage"] == "provider_preflight"
    assert plan["blocked_by"] == [
        "provider_preflight_not_verified",
        "shadow_proposal_sample_incomplete",
    ]
    assert plan["rollback_target"] == "fixture_canary"


def test_limited_canary_review_needs_ten_clean_shadow_proposals() -> None:
    incomplete = alpha_model_readiness_payload(
        _facts(model_turns=9, proposed_turns=9)
    )["promotion_dry_run"]
    complete = alpha_model_readiness_payload(
        _facts(model_turns=10, proposed_turns=10)
    )["promotion_dry_run"]

    assert isinstance(incomplete, dict)
    assert isinstance(complete, dict)
    assert incomplete["limited_canary_review_ready"] is False
    assert "shadow_proposal_sample_incomplete" in incomplete["blocked_by"]
    assert complete["limited_canary_review_ready"] is True
    assert complete["next_stage"] == "limited_canary_review"
    assert complete["mutation_allowed"] is False


@pytest.mark.parametrize(
    ("changes", "expected_state"),
    [
        (
            {"provider_preflight_successes": 1, "model_delivery_grants": 1},
            AlphaModelReadinessState.SAFETY_INVARIANT_VIOLATED,
        ),
        (
            {"model_turns": 1, "failed_turns": 1},
            AlphaModelReadinessState.PROVIDER_PREFLIGHT_FAILED,
        ),
        (
            {"stuck_provider_preflights": 1},
            AlphaModelReadinessState.PROVIDER_PREFLIGHT_STUCK,
        ),
        (
            {"reserved_microusd": 50_000_000},
            AlphaModelReadinessState.BUDGET_EXHAUSTED,
        ),
        (
            {"staged_deployments": 0},
            AlphaModelReadinessState.DEPLOYMENT_NOT_EXACT,
        ),
    ],
)
def test_operational_stop_drills_never_mutate_or_promote(
    changes: dict[str, Any],
    expected_state: AlphaModelReadinessState,
) -> None:
    facts = _facts(**changes)
    readiness = evaluate_alpha_model_readiness(facts)
    plan = alpha_model_readiness_payload(facts)["promotion_dry_run"]

    assert isinstance(plan, dict)
    assert readiness.state is expected_state
    assert plan["mutation_allowed"] is False
    assert plan["limited_canary_review_ready"] is False
    assert plan["rollback_target"] == "fixture_canary"


def test_status_payload_is_redacted_and_reports_exact_budget() -> None:
    facts = replace(_facts(), reserved_microusd=12_288)
    payload = alpha_model_readiness_payload(facts)

    assert payload["state"] == "awaiting_provider_preflight"
    assert payload["telegram_delivery_blocked"] is True
    assert payload["provider_preflight_successes"] == 0
    assert payload["provider_preflight_failures"] == 0
    assert payload["model_delivery_grants"] == 0
    assert payload["stuck_provider_preflights"] == 0
    assert payload["budget"] == {
        "monthly_microusd": 50_000_000,
        "reserved_microusd": 12_288,
        "remaining_microusd": 49_987_712,
    }
    assert "api_key" not in payload
    assert "group_id" not in payload


def test_bounded_live_phase_is_ready_without_fixture_stop() -> None:
    facts = _facts(
        fixture_binding_active=False,
        telegram_delivery_blocked=False,
        provider_preflight_successes=1,
        model_turns=15,
        proposed_turns=15,
    )

    readiness = evaluate_alpha_model_readiness(facts)
    payload = alpha_model_readiness_payload(facts)
    plan = payload["promotion_dry_run"]

    assert readiness.state is AlphaModelReadinessState.SHADOW_OBSERVED
    assert readiness.prerequisites_ready is True
    assert readiness.alerts == ()
    assert payload["fixture_binding_active"] is False
    assert payload["telegram_delivery_blocked"] is False
    assert isinstance(plan, dict)
    assert plan["current_stage"] == "bounded_live"
    assert plan["next_stage"] == "bounded_live_observation"
    assert plan["blocked_by"] == []


def test_mismatched_binding_and_delivery_phase_fails_closed() -> None:
    readiness = evaluate_alpha_model_readiness(
        _facts(
            fixture_binding_active=False,
            telegram_delivery_blocked=True,
        )
    )

    assert readiness.state is AlphaModelReadinessState.CONFIGURATION_INCOMPLETE
    assert readiness.prerequisites_ready is False


def test_multi_group_release_readiness_accepts_pending_groups_within_capacity() -> None:
    readiness = alpha_release_binding_readiness(
        discovered_installations=3,
        group_capacity=3,
        active_installations=2,
        exact_active_bindings=2,
        reviewed_model_bindings=2,
    )
    assert readiness == {
        "groups_within_capacity": True,
        "all_active_bindings_exact": True,
        "reviewed_model_release_ready": True,
    }


@pytest.mark.parametrize(
    ("active", "exact", "reviewed"),
    [(3, 2, 2), (2, 2, 1), (0, 0, 0)],
)
def test_multi_group_release_readiness_fails_closed_on_binding_gap(
    active: int, exact: int, reviewed: int,
) -> None:
    readiness = alpha_release_binding_readiness(
        discovered_installations=3,
        group_capacity=3,
        active_installations=active,
        exact_active_bindings=exact,
        reviewed_model_bindings=reviewed,
    )
    assert readiness["reviewed_model_release_ready"] is False


def test_multi_group_release_readiness_fails_closed_above_capacity() -> None:
    readiness = alpha_release_binding_readiness(
        discovered_installations=4,
        group_capacity=3,
        active_installations=2,
        exact_active_bindings=2,
        reviewed_model_bindings=2,
    )
    assert readiness["groups_within_capacity"] is False
    assert readiness["reviewed_model_release_ready"] is False
