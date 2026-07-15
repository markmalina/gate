"""Release-control contracts for Alpha model shadow execution."""

from datetime import UTC, datetime

import pytest

from app.application.agent_kernel import (
    AgentDeployment,
    AgentDeploymentState,
    InstructionRevision,
)
from app.application import alpha_shadow_worker as release_controls
from app.application.alpha_lifecycle import ReserveAlphaUsageCommand
from app.application.work_ownership import AgentKey


def _deployment(*, runtime: str, model: str) -> AgentDeployment:
    return AgentDeployment(
        deployment_key="alpha-release-control",
        agent_key=AgentKey.ALPHA_GROUP,
        instruction=InstructionRevision(
            revision_key="alpha-release-control",
            source_repository="markmalina/Gate",
            source_path="agent_bundles/alpha_model_v1",
            source_commit_sha="a" * 40,
            content_sha256="b" * 64,
        ),
        runtime_profile=runtime,
        model_profile=model,
        policy_version="alpha-model-v1",
        state=AgentDeploymentState.STAGED,
    )


def test_model_runtime_is_shadow_only_with_fixed_fifty_dollar_budget() -> None:
    deployment = _deployment(
        runtime="openai-responses-v1",
        model="gpt-5.6-luna",
    )

    assert release_controls.alpha_proposal_delivery_mode(deployment) is release_controls.AlphaProposalDeliveryMode.SHADOW_ONLY
    assert release_controls.alpha_model_usage_budget(deployment) == (
        release_controls.ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD,
        release_controls.ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN,
    )
    assert release_controls.ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD == 50_000_000


def test_network_free_fixture_keeps_existing_canary_delivery_contract() -> None:
    deployment = _deployment(runtime="fixture", model="no-network")

    assert release_controls.alpha_proposal_delivery_mode(deployment) is release_controls.AlphaProposalDeliveryMode.CANARY
    assert release_controls.alpha_model_usage_budget(deployment) is None


def test_budgeted_reservation_requires_a_positive_conservative_unit_cost() -> None:
    with pytest.raises(ValueError, match="positive max cost"):
        ReserveAlphaUsageCommand(
            workspace_key="mark-personal",
            installation_key="installation-1",
            trigger_message_id="message-1",
            reserved_tokens=2048,
            now=datetime(2026, 7, 15, tzinfo=UTC),
            monthly_budget_microusd=50_000_000,
        )
