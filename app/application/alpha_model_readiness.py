"""Pure release-state evaluation for the staged Alpha model path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MIN_SHADOW_PROPOSALS_FOR_CANARY_REVIEW = 10


class AlphaModelReadinessState(StrEnum):
    CONFIGURATION_INCOMPLETE = "configuration_incomplete"
    DEPLOYMENT_NOT_EXACT = "deployment_not_exact"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SAFETY_INVARIANT_VIOLATED = "safety_invariant_violated"
    PROVIDER_PREFLIGHT_STUCK = "provider_preflight_stuck"
    AWAITING_PROVIDER_PREFLIGHT = "awaiting_provider_preflight"
    PROVIDER_PREFLIGHT_FAILED = "provider_preflight_failed"
    PROVIDER_PREFLIGHT_VERIFIED = "provider_preflight_verified"
    SHADOW_OBSERVED = "shadow_observed"


@dataclass(frozen=True, slots=True)
class AlphaModelReadinessFacts:
    runtime_enabled: bool
    data_gate_approved: bool
    api_key_configured: bool
    staged_deployments: int
    monthly_budget_microusd: int
    reserved_microusd: int
    model_turns: int
    proposed_turns: int
    failed_turns: int
    provider_preflight_successes: int = 0
    provider_preflight_failures: int = 0
    queued_turns: int = 0
    running_turns: int = 0
    model_delivery_grants: int = 0
    stuck_provider_preflights: int = 0
    last_provider_error_code: str | None = None
    fixture_binding_active: bool = True
    telegram_delivery_blocked: bool = True


@dataclass(frozen=True, slots=True)
class AlphaModelReadiness:
    state: AlphaModelReadinessState
    prerequisites_ready: bool
    provider_preflight_verified: bool
    ready_for_provider_preflight: bool
    ready_for_shadow_observation: bool
    alerts: tuple[str, ...]
    provider_error_category: str


def evaluate_alpha_model_readiness(
    facts: AlphaModelReadinessFacts,
) -> AlphaModelReadiness:
    """Derive one closed operational state without contacting a provider."""

    shadow_phase = (
        facts.telegram_delivery_blocked and facts.fixture_binding_active
    )
    bounded_live_phase = (
        not facts.telegram_delivery_blocked
        and not facts.fixture_binding_active
    )
    configured = (
        facts.runtime_enabled
        and facts.data_gate_approved
        and facts.api_key_configured
        and (shadow_phase or bounded_live_phase)
    )
    exact_deployment = facts.staged_deployments == 1
    budget_available = (
        facts.monthly_budget_microusd > 0
        and facts.reserved_microusd < facts.monthly_budget_microusd
    )
    safety_invariants_hold = facts.model_delivery_grants == 0
    preflight_not_stuck = facts.stuck_provider_preflights == 0
    prerequisites_ready = (
        configured
        and exact_deployment
        and budget_available
        and safety_invariants_hold
        and preflight_not_stuck
    )

    if not configured:
        state = AlphaModelReadinessState.CONFIGURATION_INCOMPLETE
    elif not exact_deployment:
        state = AlphaModelReadinessState.DEPLOYMENT_NOT_EXACT
    elif not budget_available:
        state = AlphaModelReadinessState.BUDGET_EXHAUSTED
    elif not safety_invariants_hold:
        state = AlphaModelReadinessState.SAFETY_INVARIANT_VIOLATED
    elif not preflight_not_stuck:
        state = AlphaModelReadinessState.PROVIDER_PREFLIGHT_STUCK
    elif facts.proposed_turns > 0:
        state = AlphaModelReadinessState.SHADOW_OBSERVED
    elif facts.provider_preflight_successes > 0:
        state = AlphaModelReadinessState.PROVIDER_PREFLIGHT_VERIFIED
    elif facts.failed_turns > 0 or facts.provider_preflight_failures > 0:
        state = AlphaModelReadinessState.PROVIDER_PREFLIGHT_FAILED
    else:
        state = AlphaModelReadinessState.AWAITING_PROVIDER_PREFLIGHT

    provider_preflight_verified = (
        facts.proposed_turns > 0 or facts.provider_preflight_successes > 0
    )
    return AlphaModelReadiness(
        state=state,
        prerequisites_ready=prerequisites_ready,
        provider_preflight_verified=provider_preflight_verified,
        ready_for_provider_preflight=(
            prerequisites_ready and facts.model_turns == 0
        ),
        ready_for_shadow_observation=(
            prerequisites_ready and provider_preflight_verified
        ),
        alerts=_alerts(facts),
        provider_error_category=_provider_error_category(
            facts.last_provider_error_code
        ),
    )


def alpha_model_readiness_payload(
    facts: AlphaModelReadinessFacts,
) -> dict[str, object]:
    """Serialize only content-free, secret-free release evidence."""

    readiness = evaluate_alpha_model_readiness(facts)
    return {
        "state": readiness.state.value,
        "prerequisites_ready": readiness.prerequisites_ready,
        "provider_preflight_verified": readiness.provider_preflight_verified,
        "ready_for_provider_preflight": readiness.ready_for_provider_preflight,
        "ready_for_shadow_observation": readiness.ready_for_shadow_observation,
        "runtime_enabled": facts.runtime_enabled,
        "data_gate_approved": facts.data_gate_approved,
        "api_key_configured": facts.api_key_configured,
        "telegram_delivery_blocked": facts.telegram_delivery_blocked,
        "fixture_binding_active": facts.fixture_binding_active,
        "staged_deployments": facts.staged_deployments,
        "model_turns": facts.model_turns,
        "queued_turns": facts.queued_turns,
        "running_turns": facts.running_turns,
        "proposed_turns": facts.proposed_turns,
        "failed_turns": facts.failed_turns,
        "model_delivery_grants": facts.model_delivery_grants,
        "provider_preflight_successes": facts.provider_preflight_successes,
        "provider_preflight_failures": facts.provider_preflight_failures,
        "stuck_provider_preflights": facts.stuck_provider_preflights,
        "last_provider_error_category": readiness.provider_error_category,
        "alerts": list(readiness.alerts),
        "promotion_dry_run": _promotion_dry_run(facts, readiness),
        "budget": {
            "monthly_microusd": facts.monthly_budget_microusd,
            "reserved_microusd": facts.reserved_microusd,
            "remaining_microusd": max(
                0,
                facts.monthly_budget_microusd - facts.reserved_microusd,
            ),
        },
    }


def _alerts(facts: AlphaModelReadinessFacts) -> tuple[str, ...]:
    alerts: list[str] = []
    if facts.model_delivery_grants > 0:
        alerts.append("critical:model_delivery_grant_detected")
    if facts.stuck_provider_preflights > 0:
        alerts.append("critical:provider_preflight_stuck")
    if facts.failed_turns > 0:
        alerts.append("warning:model_shadow_failure")
    if facts.provider_preflight_failures > 0:
        alerts.append(
            "warning:provider_preflight_failure:"
            + _provider_error_category(facts.last_provider_error_code)
        )
    return tuple(alerts)


def _provider_error_category(code: str | None) -> str:
    if code is None:
        return "none"
    if "credential" in code:
        return "credentials"
    if "rate_limit" in code:
        return "rate_limit"
    if any(token in code for token in ("timeout", "unavailable", "request_failed")):
        return "availability"
    if "request_rejected" in code:
        return "request"
    if any(token in code for token in ("response", "usage", "capability")):
        return "contract"
    return "unknown"


def _promotion_dry_run(
    facts: AlphaModelReadinessFacts,
    readiness: AlphaModelReadiness,
) -> dict[str, object]:
    bounded_live_phase = (
        not facts.telegram_delivery_blocked
        and not facts.fixture_binding_active
    )
    if bounded_live_phase:
        current_stage = "bounded_live"
    elif facts.proposed_turns > 0:
        current_stage = "model_shadow"
    elif readiness.provider_preflight_verified:
        current_stage = "provider_preflight_verified"
    else:
        current_stage = "fixture_canary"

    if bounded_live_phase:
        next_stage = "bounded_live_observation"
    elif not readiness.provider_preflight_verified:
        next_stage = "provider_preflight"
    elif facts.proposed_turns < MIN_SHADOW_PROPOSALS_FOR_CANARY_REVIEW:
        next_stage = "model_shadow"
    else:
        next_stage = "limited_canary_review"

    blocked_by: list[str] = []
    if not readiness.prerequisites_ready:
        blocked_by.append(readiness.state.value)
    if not readiness.provider_preflight_verified:
        blocked_by.append("provider_preflight_not_verified")
    if facts.failed_turns > 0:
        blocked_by.append("model_shadow_failures_present")
    if facts.proposed_turns < MIN_SHADOW_PROPOSALS_FOR_CANARY_REVIEW:
        blocked_by.append("shadow_proposal_sample_incomplete")

    return {
        "execution_mode": "dry_run",
        "mutation_allowed": False,
        "current_stage": current_stage,
        "next_stage": next_stage,
        "blocked_by": list(dict.fromkeys(blocked_by)),
        "shadow_proposals_required": MIN_SHADOW_PROPOSALS_FOR_CANARY_REVIEW,
        "shadow_proposals_observed": facts.proposed_turns,
        "limited_canary_review_ready": (
            readiness.prerequisites_ready
            and readiness.provider_preflight_verified
            and facts.proposed_turns >= MIN_SHADOW_PROPOSALS_FOR_CANARY_REVIEW
            and facts.failed_turns == 0
        ),
        "rollback_target": "fixture_canary",
        "automatic_stop_conditions": [
            "any_model_delivery_grant",
            "any_model_shadow_failure",
            "provider_preflight_stuck",
            "monthly_budget_exhausted",
            "deployment_binding_not_exact",
        ],
    }



def alpha_release_binding_readiness(
    *,
    discovered_installations: int,
    group_capacity: int,
    active_installations: int,
    exact_active_bindings: int,
    reviewed_model_bindings: int,
) -> dict[str, bool]:
    """Evaluate release readiness across all admitted Alpha group installations.

    Pending installations inside the configured capacity do not disable exact
    active groups. Any active binding gap, unreviewed model release, empty
    active set, or capacity overflow fails closed.
    """

    groups_within_capacity = (
        1 <= group_capacity <= 10_000
        and 0 <= discovered_installations <= group_capacity
    )
    all_active_bindings_exact = (
        active_installations > 0
        and exact_active_bindings == active_installations
    )
    reviewed_model_release_ready = (
        groups_within_capacity
        and all_active_bindings_exact
        and reviewed_model_bindings == exact_active_bindings
    )
    return {
        "groups_within_capacity": groups_within_capacity,
        "all_active_bindings_exact": all_active_bindings_exact,
        "reviewed_model_release_ready": reviewed_model_release_ready,
    }
