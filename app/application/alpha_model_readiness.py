"""Pure release-state evaluation for the staged Alpha model path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AlphaModelReadinessState(StrEnum):
    CONFIGURATION_INCOMPLETE = "configuration_incomplete"
    DEPLOYMENT_NOT_EXACT = "deployment_not_exact"
    BUDGET_EXHAUSTED = "budget_exhausted"
    AWAITING_PROVIDER_PREFLIGHT = "awaiting_provider_preflight"
    PROVIDER_PREFLIGHT_FAILED = "provider_preflight_failed"
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
    telegram_delivery_blocked: bool = True


@dataclass(frozen=True, slots=True)
class AlphaModelReadiness:
    state: AlphaModelReadinessState
    prerequisites_ready: bool
    provider_preflight_verified: bool
    ready_for_provider_preflight: bool
    ready_for_shadow_observation: bool


def evaluate_alpha_model_readiness(
    facts: AlphaModelReadinessFacts,
) -> AlphaModelReadiness:
    """Derive one closed operational state without contacting a provider."""

    configured = (
        facts.runtime_enabled
        and facts.data_gate_approved
        and facts.api_key_configured
        and facts.telegram_delivery_blocked
    )
    exact_deployment = facts.staged_deployments == 1
    budget_available = (
        facts.monthly_budget_microusd > 0
        and facts.reserved_microusd < facts.monthly_budget_microusd
    )
    prerequisites_ready = configured and exact_deployment and budget_available

    if not configured:
        state = AlphaModelReadinessState.CONFIGURATION_INCOMPLETE
    elif not exact_deployment:
        state = AlphaModelReadinessState.DEPLOYMENT_NOT_EXACT
    elif not budget_available:
        state = AlphaModelReadinessState.BUDGET_EXHAUSTED
    elif facts.proposed_turns > 0:
        state = AlphaModelReadinessState.SHADOW_OBSERVED
    elif facts.failed_turns > 0:
        state = AlphaModelReadinessState.PROVIDER_PREFLIGHT_FAILED
    else:
        state = AlphaModelReadinessState.AWAITING_PROVIDER_PREFLIGHT

    provider_preflight_verified = facts.proposed_turns > 0
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
        "staged_deployments": facts.staged_deployments,
        "model_turns": facts.model_turns,
        "proposed_turns": facts.proposed_turns,
        "failed_turns": facts.failed_turns,
        "budget": {
            "monthly_microusd": facts.monthly_budget_microusd,
            "reserved_microusd": facts.reserved_microusd,
            "remaining_microusd": max(
                0,
                facts.monthly_budget_microusd - facts.reserved_microusd,
            ),
        },
    }
