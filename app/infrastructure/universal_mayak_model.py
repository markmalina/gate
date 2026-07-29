"""Universal Mayak adapter over Gate's existing AgentRuntimePort.

The adapter deliberately contains no provider client. It translates the bounded
Mayak model request to Gate's already reviewed agent runtime contract, which is
responsible for the OpenAI Responses call and its data gates.
"""

from __future__ import annotations

import httpx

from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    AgentRuntimePort,
    AgentSession,
    AgentTurnCommand,
)
from app.application.instruction_bundles import LoadedInstructionBundle
from app.application.universal_mayak_demo import (
    UniversalMayakDemoError,
    UniversalMayakModelPort,
    UniversalMayakModelReply,
    UniversalMayakModelRequest,
)
from app.application.work_ownership import AgentKey
from app.infrastructure.openai_agent_runtime import OpenAIResponsesAgentRuntime

_LOCAL_TURN_KEY = "universal-mayak-turn"
_LOCAL_SESSION_KEY = "universal-mayak-session"
_LOCAL_WORKSPACE_ID = "universal-mayak-workspace"
_LOCAL_DIALOG_ID = "universal-mayak-dialog"
_LOCAL_TRIGGER_ID = "universal-mayak-trigger"


class AgentRuntimeUniversalMayakModelAdapter(UniversalMayakModelPort):
    """Project one Mayak request through the existing structured agent runtime.

    Runtime-local identifiers are fixed non-provider tokens. The existing
    OpenAI runtime serializes only context text plus the current user text, so
    workspace, installation, dialog, message and Telegram identifiers never
    enter the provider request.
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntimePort,
        deployment: AgentDeployment,
    ) -> None:
        self._runtime = runtime
        self._deployment = deployment

    async def propose(
        self,
        request: UniversalMayakModelRequest,
    ) -> UniversalMayakModelReply:
        revision = self._deployment.instruction
        if request.prompt_ref != revision.revision_key:
            raise UniversalMayakDemoError("mayak_model_revision_mismatch")
        if request.prompt_sha256 != revision.content_sha256:
            raise UniversalMayakDemoError("mayak_model_digest_mismatch")

        result = await self._runtime.propose(
            AgentTurnCommand(
                turn_key=_LOCAL_TURN_KEY,
                session=AgentSession(
                    session_key=_LOCAL_SESSION_KEY,
                    workspace_id=_LOCAL_WORKSPACE_ID,
                    dialog_id=_LOCAL_DIALOG_ID,
                    deployment=self._deployment,
                ),
                trigger_message_id=_LOCAL_TRIGGER_ID,
                user_text=request.user_text,
                context=tuple(
                    AgentContextMessage(
                        message_id=f"mayak-context-{index}",
                        role=message.role,
                        content=message.text,
                    )
                    for index, message in enumerate(request.context)
                ),
            )
        )
        if result.kind is not AgentResultKind.REPLY:
            raise UniversalMayakDemoError("mayak_model_non_reply")
        if result.candidate_text is None:
            raise UniversalMayakDemoError("mayak_model_reply_missing")
        return UniversalMayakModelReply(
            text=result.candidate_text,
            result_code=result.result_code,
        )


def build_configured_universal_mayak_model(
    *,
    bundle: LoadedInstructionBundle,
    http_client: httpx.AsyncClient,
    api_key: str,
    api_base_url: str,
    model_profile: str,
    runtime_enabled: bool,
    data_gate_approved: bool,
    max_output_tokens: int,
    timeout_seconds: float,
) -> AgentRuntimeUniversalMayakModelAdapter:
    """Build the Mayak adapter over Gate's one existing Responses runtime."""

    if not runtime_enabled:
        raise UniversalMayakDemoError("mayak_model_runtime_disabled")
    if not data_gate_approved:
        raise UniversalMayakDemoError("mayak_model_data_gate_not_approved")
    if bundle.agent_key is not AgentKey.OKO:
        raise UniversalMayakDemoError("mayak_model_bundle_agent_mismatch")

    deployment = AgentDeployment(
        deployment_key="universal-mayak-oko-v2-openai",
        agent_key=AgentKey.OKO,
        instruction=bundle.revision,
        runtime_profile="openai-responses-v1",
        model_profile=model_profile,
        policy_version="universal-mayak-oko-v2",
        state=AgentDeploymentState.ACTIVE,
    )
    runtime = OpenAIResponsesAgentRuntime(
        bundle=bundle,
        http_client=http_client,
        api_key=api_key,
        model=model_profile,
        api_base_url=api_base_url,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        data_gate_approved=data_gate_approved,
    )
    return AgentRuntimeUniversalMayakModelAdapter(
        runtime=runtime,
        deployment=deployment,
    )
