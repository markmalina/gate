"""Universal Mayak adapter over Gate's existing AgentRuntimePort.

The adapter deliberately contains no provider client. It translates the bounded
Mayak model request to Gate's already reviewed agent runtime contract, which is
responsible for the OpenAI Responses call and its data gates.
"""

from __future__ import annotations

from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentResultKind,
    AgentRuntimePort,
    AgentSession,
    AgentTurnCommand,
)
from app.application.universal_mayak_demo import (
    UniversalMayakDemoError,
    UniversalMayakModelPort,
    UniversalMayakModelReply,
    UniversalMayakModelRequest,
)

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
