"""Workspace- and installation-scoped Alpha shadow review queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_shadow_reviews import AlphaShadowReview
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
)
from app.models.alpha import AgentInstallationModel
from app.models.canonical import CanonicalMessage, Workspace

_MODEL_RUNTIME_PROFILE = "openai-responses-v1"


async def list_alpha_shadow_reviews(
    session: AsyncSession,
    *,
    workspace_key: str,
    limit: int,
) -> tuple[AlphaShadowReview, ...]:
    """List only model-backed turns from approved Alpha group installations."""

    bounded_limit = max(1, min(limit, 50))
    rows = (
        await session.execute(
            select(
                AgentTurnModel,
                AgentDeploymentModel.deployment_key,
                AgentInstallationModel.installation_key,
                AgentInstallationModel.dialog_label,
                CanonicalMessage.text_content,
            )
            .join(
                AgentSessionModel,
                (AgentSessionModel.id == AgentTurnModel.session_id)
                & (AgentSessionModel.workspace_id == AgentTurnModel.workspace_id),
            )
            .join(
                AgentDeploymentModel,
                (AgentDeploymentModel.id == AgentSessionModel.deployment_id)
                & (AgentDeploymentModel.workspace_id == AgentSessionModel.workspace_id),
            )
            .join(
                AgentDefinitionModel,
                (AgentDefinitionModel.id == AgentDeploymentModel.agent_definition_id)
                & (AgentDefinitionModel.workspace_id == AgentDeploymentModel.workspace_id),
            )
            .join(
                AgentInstallationModel,
                (AgentInstallationModel.dialog_id == AgentSessionModel.dialog_id)
                & (AgentInstallationModel.workspace_id == AgentSessionModel.workspace_id),
            )
            .join(
                CanonicalMessage,
                (CanonicalMessage.id == AgentTurnModel.trigger_message_id)
                & (CanonicalMessage.workspace_id == AgentTurnModel.workspace_id),
            )
            .join(Workspace, Workspace.id == AgentTurnModel.workspace_id)
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AgentDefinitionModel.agent_key == "alpha_group",
                AgentDeploymentModel.runtime_profile == _MODEL_RUNTIME_PROFILE,
                AgentInstallationModel.state.in_(("active", "paused")),
            )
            .order_by(AgentTurnModel.created_at.desc(), AgentTurnModel.id.desc())
            .limit(bounded_limit)
        )
    ).all()
    return tuple(
        AlphaShadowReview(
            turn_ref=str(turn.id),
            installation_ref=installation_key,
            group_label=dialog_label,
            deployment_key=deployment_key,
            status=turn.status,
            outcome=turn.result_kind,
            result_code=turn.result_code,
            trigger_text=trigger_text,
            candidate_text=turn.candidate_text,
            candidate_sha256=turn.candidate_sha256,
            context_message_count=len(turn.context_message_ids),
            created_at=turn.created_at,
        )
        for turn, deployment_key, installation_key, dialog_label, trigger_text in rows
    )
