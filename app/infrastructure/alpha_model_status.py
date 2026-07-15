"""Content-free operational telemetry for the staged Alpha model path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_shadow_worker import (
    ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD,
    ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN,
)
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)
from app.models.alpha import AgentInstallationModel, AlphaUsageRecordModel
from app.models.alpha_preflight import AlphaModelPreflightModel
from app.models.canonical import Workspace

_OPENAI_RUNTIME_PROFILE = "openai-responses-v1"
_OPENAI_MODEL_PROFILE = "gpt-5.6-luna"


@dataclass(frozen=True, slots=True)
class AlphaModelOperationalStatus:
    staged_deployments: int
    model_turns: int
    proposed_turns: int
    failed_turns: int
    reserved_tokens: int
    reserved_microusd: int
    remaining_microusd: int
    provider_preflight_successes: int
    provider_preflight_failures: int
    monthly_budget_microusd: int = ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD


async def alpha_model_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    now: datetime | None = None,
) -> AlphaModelOperationalStatus:
    """Return redacted model release evidence scoped to one active workspace."""

    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    month_start = observed_at.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    staged = await session.scalar(
        select(func.count())
        .select_from(AgentDeploymentModel)
        .join(Workspace, Workspace.id == AgentDeploymentModel.workspace_id)
        .join(
            AgentDefinitionModel,
            (AgentDefinitionModel.id == AgentDeploymentModel.agent_definition_id)
            & (AgentDefinitionModel.workspace_id == AgentDeploymentModel.workspace_id),
        )
        .join(
            InstructionRevisionModel,
            (InstructionRevisionModel.id == AgentDeploymentModel.instruction_revision_id)
            & (InstructionRevisionModel.workspace_id == AgentDeploymentModel.workspace_id),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AgentDefinitionModel.agent_key == "alpha_group",
            AgentDeploymentModel.runtime_profile == _OPENAI_RUNTIME_PROFILE,
            AgentDeploymentModel.model_profile == _OPENAI_MODEL_PROFILE,
            AgentDeploymentModel.state == "staged",
            InstructionRevisionModel.validation_state == "validated",
        )
    )
    model_turns = await _model_turn_count(
        session,
        workspace_key=workspace_key,
        status=None,
    )
    proposed_turns = await _model_turn_count(
        session,
        workspace_key=workspace_key,
        status="proposed",
    )
    failed_turns = await _model_turn_count(
        session,
        workspace_key=workspace_key,
        status="failed",
    )
    reserved_tokens = await session.scalar(
        select(func.coalesce(func.sum(AlphaUsageRecordModel.reserved_tokens), 0))
        .join(
            AgentInstallationModel,
            (AgentInstallationModel.id == AlphaUsageRecordModel.installation_id)
            & (AgentInstallationModel.workspace_id == AlphaUsageRecordModel.workspace_id),
        )
        .join(
            AgentDeploymentModel,
            (AgentDeploymentModel.id == AgentInstallationModel.deployment_id)
            & (AgentDeploymentModel.workspace_id == AgentInstallationModel.workspace_id),
        )
        .join(Workspace, Workspace.id == AlphaUsageRecordModel.workspace_id)
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AgentDeploymentModel.runtime_profile == _OPENAI_RUNTIME_PROFILE,
            AgentDeploymentModel.model_profile == _OPENAI_MODEL_PROFILE,
            AlphaUsageRecordModel.state != "released",
            AlphaUsageRecordModel.created_at >= month_start,
        )
    )
    preflight_reserved_tokens = await session.scalar(
        select(
            func.coalesce(
                func.sum(AlphaModelPreflightModel.reserved_tokens),
                0,
            )
        )
        .join(Workspace, Workspace.id == AlphaModelPreflightModel.workspace_id)
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AlphaModelPreflightModel.created_at >= month_start,
        )
    )
    provider_preflight_successes = await session.scalar(
        select(func.count())
        .select_from(AlphaModelPreflightModel)
        .join(Workspace, Workspace.id == AlphaModelPreflightModel.workspace_id)
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AlphaModelPreflightModel.state == "succeeded",
        )
    )
    provider_preflight_failures = await session.scalar(
        select(func.count())
        .select_from(AlphaModelPreflightModel)
        .join(Workspace, Workspace.id == AlphaModelPreflightModel.workspace_id)
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AlphaModelPreflightModel.state == "failed",
        )
    )
    token_count = int(reserved_tokens or 0) + int(preflight_reserved_tokens or 0)
    reserved_microusd = token_count * ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN
    return AlphaModelOperationalStatus(
        staged_deployments=int(staged or 0),
        model_turns=model_turns,
        proposed_turns=proposed_turns,
        failed_turns=failed_turns,
        reserved_tokens=token_count,
        reserved_microusd=reserved_microusd,
        remaining_microusd=max(
            0,
            ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD - reserved_microusd,
        ),
        provider_preflight_successes=int(provider_preflight_successes or 0),
        provider_preflight_failures=int(provider_preflight_failures or 0),
    )


async def _model_turn_count(
    session: AsyncSession,
    *,
    workspace_key: str,
    status: str | None,
) -> int:
    statement = (
        select(func.count())
        .select_from(AgentTurnModel)
        .join(AgentSessionModel, AgentSessionModel.id == AgentTurnModel.session_id)
        .join(
            AgentDeploymentModel,
            AgentDeploymentModel.id == AgentSessionModel.deployment_id,
        )
        .join(Workspace, Workspace.id == AgentTurnModel.workspace_id)
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            AgentSessionModel.workspace_id == AgentTurnModel.workspace_id,
            AgentDeploymentModel.workspace_id == AgentTurnModel.workspace_id,
            AgentDeploymentModel.runtime_profile == _OPENAI_RUNTIME_PROFILE,
            AgentDeploymentModel.model_profile == _OPENAI_MODEL_PROFILE,
        )
    )
    if status is not None:
        statement = statement.where(AgentTurnModel.status == status)
    count = await session.scalar(statement)
    return int(count or 0)
