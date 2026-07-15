"""Traceable Agent Kernel shadow proposals with no persistence or delivery effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.application.agent_kernel import (
    AgentDeploymentState,
    AgentResultKind,
    AgentRuntimePort,
    AgentTurnCommand,
)


class AgentShadowError(ValueError):
    """A bounded shadow execution refusal."""


@dataclass(frozen=True, slots=True)
class AgentShadowProposal:
    """Auditable proposal identity that carries no transport target."""

    turn_key: str
    deployment_key: str
    instruction_commit_sha: str
    instruction_content_sha256: str
    policy_version: str
    result_kind: AgentResultKind
    result_code: str
    candidate_text: str | None = field(default=None, repr=False)
    candidate_sha256: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class RunAgentShadowTurnService:
    """Run one pinned staged/active turn and return delivery-free trace metadata."""

    def __init__(self, runtime: AgentRuntimePort) -> None:
        self._runtime = runtime

    async def execute(self, command: AgentTurnCommand) -> AgentShadowProposal:
        deployment = command.session.deployment
        if deployment.state is AgentDeploymentState.PAUSED:
            raise AgentShadowError("agent_deployment_paused")
        result = await self._runtime.propose(command)
        candidate_digest = (
            hashlib.sha256(result.candidate_text.encode("utf-8")).hexdigest()
            if result.candidate_text is not None
            else None
        )
        return AgentShadowProposal(
            turn_key=command.turn_key,
            deployment_key=deployment.deployment_key,
            instruction_commit_sha=deployment.instruction.source_commit_sha,
            instruction_content_sha256=deployment.instruction.content_sha256,
            policy_version=deployment.policy_version,
            result_kind=result.kind,
            result_code=result.result_code,
            candidate_text=result.candidate_text,
            candidate_sha256=candidate_digest,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
