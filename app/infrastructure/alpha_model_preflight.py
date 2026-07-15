"""One-shot, delivery-free Alpha provider preflight."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent_kernel import (
    AgentDeployment,
    AgentDeploymentState,
    AgentSession,
    AgentTurnCommand,
    InstructionRevision,
)
from app.application.alpha_shadow_worker import (
    ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD,
    ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN,
)
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.application.work_ownership import AgentKey
from app.core.config import Settings
from app.infrastructure.openai_agent_runtime import (
    OpenAIAgentRuntimeError,
    OpenAIResponsesAgentRuntime,
)
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
)
from app.models.alpha import AgentInstallationModel, AlphaUsageRecordModel
from app.models.alpha_preflight import AlphaModelPreflightModel
from app.models.canonical import Workspace

PREFLIGHT_KEY = "alpha-model-v1-openai-preflight-v1"
PREFLIGHT_RESERVED_TOKENS = 2_048
PREFLIGHT_MAX_OUTPUT_TOKENS = 64
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_DEPLOYMENT_KEY = "alpha-model-v1-openai"
_REVISION_KEY = "alpha-model-v1"
_SYNTHETIC_REQUEST = "/alpha Ответь одним словом: готово."


class AlphaModelPreflightError(RuntimeError):
    """Bounded precondition failure before any provider call."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def run_alpha_model_preflight(
    session: AsyncSession,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    workspace_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attempt the exact provider release once and return content-free evidence."""

    _require_configuration(settings)
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    existing = await _existing_preflight(session, workspace_key=workspace_key)
    if existing is not None:
        return _serialize(existing, replay=True)

    binding = await _exact_binding(session, workspace_key=workspace_key)
    runtime, command = _runtime_and_command(
        settings=settings,
        http_client=http_client,
        workspace_key=workspace_key,
        binding=binding,
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"{workspace_key}:{PREFLIGHT_KEY}"},
    )
    existing = await _existing_preflight(session, workspace_key=workspace_key)
    if existing is not None:
        await session.commit()
        return _serialize(existing, replay=True)
    await _require_budget(
        session,
        workspace_id=binding["workspace"].id,
        observed_at=observed_at,
    )

    row = AlphaModelPreflightModel(
        workspace_id=binding["workspace"].id,
        deployment_id=binding["deployment"].id,
        instruction_revision_id=binding["revision"].id,
        preflight_key=PREFLIGHT_KEY,
        state="reserved",
        reserved_tokens=PREFLIGHT_RESERVED_TOKENS,
    )
    session.add(row)
    await session.commit()

    try:
        result = await runtime.propose(command)
    except OpenAIAgentRuntimeError as exc:
        locked = await _locked_preflight(session, row.id)
        if locked.state == "reserved":
            locked.state = "failed"
            locked.error_code = exc.code
            locked.updated_at = observed_at
        await session.commit()
        return _serialize(locked, replay=False)

    locked = await _locked_preflight(session, row.id)
    if locked.state != "reserved":
        await session.rollback()
        raise AlphaModelPreflightError("alpha_provider_preflight_state_conflict")
    locked.state = "succeeded"
    locked.input_tokens = result.input_tokens
    locked.output_tokens = result.output_tokens
    locked.result_kind = result.kind.value
    locked.result_code = result.result_code
    locked.candidate_sha256 = (
        hashlib.sha256(result.candidate_text.encode("utf-8")).hexdigest()
        if result.candidate_text is not None
        else None
    )
    locked.updated_at = observed_at
    await session.commit()
    return _serialize(locked, replay=False)


def _require_configuration(settings: Settings) -> None:
    if not settings.gate_alpha_model_runtime_enabled:
        raise AlphaModelPreflightError("alpha_model_runtime_disabled")
    if not settings.gate_alpha_model_data_gate_approved:
        raise AlphaModelPreflightError("alpha_model_data_gate_not_approved")
    if not settings.openai_api_key.get_secret_value():
        raise AlphaModelPreflightError("alpha_model_credentials_missing")
    if settings.openai_api_base_url.rstrip("/") != "https://api.openai.com/v1":
        raise AlphaModelPreflightError("alpha_model_api_base_not_approved")


async def _exact_binding(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                Workspace,
                AgentDefinitionModel,
                AgentDeploymentModel,
                InstructionRevisionModel,
            )
            .join(
                AgentDefinitionModel,
                AgentDefinitionModel.workspace_id == Workspace.id,
            )
            .join(
                AgentDeploymentModel,
                (AgentDeploymentModel.workspace_id == Workspace.id)
                & (
                    AgentDeploymentModel.agent_definition_id
                    == AgentDefinitionModel.id
                ),
            )
            .join(
                InstructionRevisionModel,
                (InstructionRevisionModel.workspace_id == Workspace.id)
                & (
                    InstructionRevisionModel.id
                    == AgentDeploymentModel.instruction_revision_id
                ),
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AgentDefinitionModel.agent_key == "alpha_group",
                AgentDeploymentModel.deployment_key == _DEPLOYMENT_KEY,
                AgentDeploymentModel.runtime_profile == _RUNTIME_PROFILE,
                AgentDeploymentModel.model_profile == _MODEL_PROFILE,
                AgentDeploymentModel.state == "staged",
                InstructionRevisionModel.revision_key == _REVISION_KEY,
                InstructionRevisionModel.validation_state == "validated",
            )
        )
    ).all()
    if len(rows) != 1:
        raise AlphaModelPreflightError("alpha_model_deployment_not_exact")
    workspace, definition, deployment, revision = rows[0]
    return {
        "workspace": workspace,
        "definition": definition,
        "deployment": deployment,
        "revision": revision,
    }


async def _require_budget(
    session: AsyncSession,
    *,
    workspace_id: object,
    observed_at: datetime,
) -> None:
    month_start = observed_at.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    group_tokens = await session.scalar(
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
        .where(
            AlphaUsageRecordModel.workspace_id == workspace_id,
            AgentDeploymentModel.runtime_profile == _RUNTIME_PROFILE,
            AgentDeploymentModel.model_profile == _MODEL_PROFILE,
            AlphaUsageRecordModel.state != "released",
            AlphaUsageRecordModel.created_at >= month_start,
        )
    )
    preflight_tokens = await session.scalar(
        select(
            func.coalesce(
                func.sum(AlphaModelPreflightModel.reserved_tokens),
                0,
            )
        ).where(
            AlphaModelPreflightModel.workspace_id == workspace_id,
            AlphaModelPreflightModel.created_at >= month_start,
        )
    )
    projected = (
        int(group_tokens or 0)
        + int(preflight_tokens or 0)
        + PREFLIGHT_RESERVED_TOKENS
    ) * ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN
    if projected > ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD:
        raise AlphaModelPreflightError("alpha_model_monthly_budget_exceeded")


def _runtime_and_command(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    workspace_key: str,
    binding: dict[str, Any],
) -> tuple[OpenAIResponsesAgentRuntime, AgentTurnCommand]:
    revision_row = binding["revision"]
    deployment_row = binding["deployment"]
    revision = InstructionRevision(
        revision_key=revision_row.revision_key,
        source_repository=revision_row.source_repository,
        source_path=revision_row.source_path,
        source_commit_sha=revision_row.source_commit_sha,
        content_sha256=revision_row.content_sha256,
        schema_version=revision_row.schema_version,
    )
    bundle = DeployedInstructionBundleLoader(
        Path(settings.gate_agent_bundle_root)
    ).load(revision.source_path)
    if bundle.revision != revision or bundle.agent_key is not AgentKey.ALPHA_GROUP:
        raise AlphaModelPreflightError("alpha_model_bundle_binding_mismatch")
    deployment = AgentDeployment(
        deployment_key=deployment_row.deployment_key,
        agent_key=AgentKey.ALPHA_GROUP,
        instruction=revision,
        runtime_profile=deployment_row.runtime_profile,
        model_profile=deployment_row.model_profile,
        policy_version=deployment_row.policy_version,
        state=AgentDeploymentState.STAGED,
    )
    runtime = OpenAIResponsesAgentRuntime(
        bundle=bundle,
        http_client=http_client,
        api_key=settings.openai_api_key.get_secret_value(),
        model=deployment.model_profile,
        api_base_url=settings.openai_api_base_url,
        max_output_tokens=PREFLIGHT_MAX_OUTPUT_TOKENS,
        timeout_seconds=settings.gate_alpha_model_timeout_seconds,
        data_gate_approved=settings.gate_alpha_model_data_gate_approved,
    )
    command = AgentTurnCommand(
        turn_key=PREFLIGHT_KEY,
        session=AgentSession(
            session_key=PREFLIGHT_KEY,
            workspace_id=workspace_key,
            dialog_id="provider-preflight",
            deployment=deployment,
        ),
        trigger_message_id="provider-preflight",
        user_text=_SYNTHETIC_REQUEST,
    )
    return runtime, command


async def _existing_preflight(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> AlphaModelPreflightModel | None:
    return (
        await session.execute(
            select(AlphaModelPreflightModel)
            .join(
                Workspace,
                Workspace.id == AlphaModelPreflightModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AlphaModelPreflightModel.preflight_key == PREFLIGHT_KEY,
            )
        )
    ).scalar_one_or_none()


async def _locked_preflight(
    session: AsyncSession,
    preflight_id: object,
) -> AlphaModelPreflightModel:
    row = (
        await session.execute(
            select(AlphaModelPreflightModel)
            .where(AlphaModelPreflightModel.id == preflight_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise AlphaModelPreflightError("alpha_provider_preflight_missing")
    return row


def _serialize(
    row: AlphaModelPreflightModel,
    *,
    replay: bool,
) -> dict[str, Any]:
    return {
        "preflight_key": row.preflight_key,
        "state": row.state,
        "replay": replay,
        "telegram_delivery_blocked": True,
        "provider_attempts": 1,
        "reserved_microusd": (
            row.reserved_tokens * ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN
        ),
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "result_kind": row.result_kind,
        "result_code": row.result_code,
        "candidate_sha256": row.candidate_sha256,
        "error_code": row.error_code,
    }
