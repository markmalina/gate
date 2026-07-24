"""Fail-closed Alpha model shadow release admission tests."""

from dataclasses import replace

from app.application.alpha_shadow_release_admission import (
    AlphaShadowAdmissionFacts,
    AlphaShadowAdmissionState,
    alpha_shadow_admission_payload,
    evaluate_alpha_shadow_admission,
)


def _facts(**overrides: object) -> AlphaShadowAdmissionFacts:
    facts = AlphaShadowAdmissionFacts(
        provider_preflight_verified=False,
        provider_preflight_failures=0,
        staged_deployments=1,
        approved_installations=1,
        deployment_key="alpha-model-v2-openai",
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
    return replace(facts, **overrides)


def test_prebilling_state_waits_without_provider_or_telegram_authority() -> None:
    admission = evaluate_alpha_shadow_admission(_facts())

    assert admission.state is AlphaShadowAdmissionState.AWAITING_PROVIDER_PREFLIGHT
    assert admission.reviewed_binding_change_allowed is False
    assert admission.provider_call_allowed is False
    assert admission.telegram_delivery_allowed is False


def test_verified_preflight_requires_reviewed_release_not_automatic_activation() -> None:
    admission = evaluate_alpha_shadow_admission(
        _facts(provider_preflight_verified=True)
    )

    assert admission.state is AlphaShadowAdmissionState.REVIEWED_RELEASE_REQUIRED
    assert admission.reviewed_binding_change_allowed is True
    assert admission.automatic_activation_allowed is False
    assert admission.telegram_delivery_allowed is False


def test_any_model_delivery_grant_blocks_the_release() -> None:
    admission = evaluate_alpha_shadow_admission(
        _facts(provider_preflight_verified=True, model_delivery_grants=1)
    )

    assert admission.state is AlphaShadowAdmissionState.SAFETY_BLOCKED
    assert admission.reviewed_binding_change_allowed is False
    assert "model_delivery_grant_observed" in admission.blocked_by


def test_exact_single_deployment_and_installation_are_required() -> None:
    admission = evaluate_alpha_shadow_admission(
        _facts(
            provider_preflight_verified=True,
            staged_deployments=2,
            approved_installations=2,
        )
    )

    assert admission.state is AlphaShadowAdmissionState.CONFIGURATION_BLOCKED
    assert "staged_deployment_not_exact" in admission.blocked_by
    assert "approved_installation_not_exact" in admission.blocked_by


def test_all_ci_evidence_including_restore_is_required() -> None:
    admission = evaluate_alpha_shadow_admission(
        _facts(provider_preflight_verified=True, restore_evidence_passed=False)
    )

    assert admission.state is AlphaShadowAdmissionState.EVIDENCE_INCOMPLETE
    assert admission.blocked_by == ("restore_evidence_missing",)


def test_payload_is_content_free_and_cannot_mutate() -> None:
    payload = alpha_shadow_admission_payload(
        evaluate_alpha_shadow_admission(_facts(provider_preflight_verified=True))
    )
    serialized = str(payload).casefold()

    assert payload["execution_mode"] == "dry_run"
    assert payload["automatic_activation_allowed"] is False
    assert "group_id" not in serialized
    assert "message_text" not in serialized
    assert "provider_response" not in serialized
