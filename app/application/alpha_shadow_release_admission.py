"""Fail-closed admission contract for Alpha model shadow releases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

TARGET_DEPLOYMENT_KEY = "alpha-model-v2-openai"
TARGET_RUNTIME_PROFILE = "openai-responses-v1"
TARGET_MODEL_PROFILE = "gpt-5.6-luna"


class AlphaShadowAdmissionState(StrEnum):
    SAFETY_BLOCKED = "safety_blocked"
    CONFIGURATION_BLOCKED = "configuration_blocked"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    BUDGET_BLOCKED = "budget_blocked"
    AWAITING_PROVIDER_PREFLIGHT = "awaiting_provider_preflight"
    REVIEWED_RELEASE_REQUIRED = "reviewed_release_required"


@dataclass(frozen=True, slots=True)
class AlphaShadowAdmissionFacts:
    provider_preflight_verified: bool
    provider_preflight_failures: int
    staged_deployments: int
    approved_installations: int
    deployment_key: str
    runtime_profile: str
    model_profile: str
    fixture_binding_active: bool
    telegram_delivery_blocked: bool
    model_delivery_grants: int
    stuck_provider_preflights: int
    failed_model_turns: int
    budget_remaining_microusd: int
    release_evidence_passed: bool
    incident_evidence_passed: bool
    retention_evidence_passed: bool
    restore_evidence_passed: bool
    supply_chain_evidence_passed: bool


@dataclass(frozen=True, slots=True)
class AlphaShadowAdmission:
    state: AlphaShadowAdmissionState
    blocked_by: tuple[str, ...]
    reviewed_binding_change_allowed: bool
    automatic_activation_allowed: bool
    provider_call_allowed: bool
    telegram_delivery_allowed: bool
    rollback_target: str


def evaluate_alpha_shadow_admission(
    facts: AlphaShadowAdmissionFacts,
) -> AlphaShadowAdmission:
    """Evaluate release admission without changing a binding or calling a provider."""

    safety_blockers: list[str] = []
    if not facts.fixture_binding_active:
        safety_blockers.append("fixture_rollback_unavailable")
    if not facts.telegram_delivery_blocked:
        safety_blockers.append("telegram_delivery_not_blocked")
    if facts.model_delivery_grants != 0:
        safety_blockers.append("model_delivery_grant_observed")
    if facts.provider_preflight_failures != 0:
        safety_blockers.append("provider_preflight_failure_observed")
    if facts.stuck_provider_preflights != 0:
        safety_blockers.append("provider_preflight_stuck")
    if facts.failed_model_turns != 0:
        safety_blockers.append("model_turn_failure_observed")

    configuration_blockers: list[str] = []
    if facts.staged_deployments != 1:
        configuration_blockers.append("staged_deployment_not_exact")
    if facts.approved_installations != 1:
        configuration_blockers.append("approved_installation_not_exact")
    if facts.deployment_key != TARGET_DEPLOYMENT_KEY:
        configuration_blockers.append("deployment_key_not_exact")
    if facts.runtime_profile != TARGET_RUNTIME_PROFILE:
        configuration_blockers.append("runtime_profile_not_exact")
    if facts.model_profile != TARGET_MODEL_PROFILE:
        configuration_blockers.append("model_profile_not_exact")

    evidence = {
        "release_evidence_missing": facts.release_evidence_passed,
        "incident_evidence_missing": facts.incident_evidence_passed,
        "retention_evidence_missing": facts.retention_evidence_passed,
        "restore_evidence_missing": facts.restore_evidence_passed,
        "supply_chain_evidence_missing": facts.supply_chain_evidence_passed,
    }
    evidence_blockers = [name for name, passed in evidence.items() if not passed]

    if safety_blockers:
        state = AlphaShadowAdmissionState.SAFETY_BLOCKED
        blocked_by = tuple(safety_blockers)
    elif configuration_blockers:
        state = AlphaShadowAdmissionState.CONFIGURATION_BLOCKED
        blocked_by = tuple(configuration_blockers)
    elif evidence_blockers:
        state = AlphaShadowAdmissionState.EVIDENCE_INCOMPLETE
        blocked_by = tuple(evidence_blockers)
    elif facts.budget_remaining_microusd <= 0:
        state = AlphaShadowAdmissionState.BUDGET_BLOCKED
        blocked_by = ("monthly_budget_exhausted",)
    elif not facts.provider_preflight_verified:
        state = AlphaShadowAdmissionState.AWAITING_PROVIDER_PREFLIGHT
        blocked_by = ("provider_preflight_not_verified",)
    else:
        state = AlphaShadowAdmissionState.REVIEWED_RELEASE_REQUIRED
        blocked_by = ("reviewed_binding_release_required",)

    return AlphaShadowAdmission(
        state=state,
        blocked_by=blocked_by,
        reviewed_binding_change_allowed=(
            state is AlphaShadowAdmissionState.REVIEWED_RELEASE_REQUIRED
        ),
        automatic_activation_allowed=False,
        provider_call_allowed=False,
        telegram_delivery_allowed=False,
        rollback_target="fixture_canary",
    )


def alpha_shadow_admission_payload(
    admission: AlphaShadowAdmission,
) -> dict[str, object]:
    """Serialize content-free admission evidence."""

    return {
        "schema_version": "alpha-shadow-admission-v1",
        "execution_mode": "dry_run",
        "state": admission.state.value,
        "blocked_by": list(admission.blocked_by),
        "reviewed_binding_change_allowed": admission.reviewed_binding_change_allowed,
        "automatic_activation_allowed": admission.automatic_activation_allowed,
        "provider_call_allowed": admission.provider_call_allowed,
        "telegram_delivery_allowed": admission.telegram_delivery_allowed,
        "rollback_target": admission.rollback_target,
    }
