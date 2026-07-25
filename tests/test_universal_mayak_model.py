"""Contract tests for the Universal Mayak model adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.agent_kernel import (
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    AgentRuntimeResult,
    AgentTurnCommand,
    InstructionRevision,
)
from app.application.universal_mayak_demo import (
    UNIVERSAL_MAYAK_PROMPT_REF,
    UNIVERSAL_MAYAK_PROMPT_SHA256,
    DemoContextMessage,
    DemoScope,
    UniversalMayakDemoError,
    UniversalMayakModelRequest,
)
from app.application.work_ownership import AgentKey
from app.infrastructure.universal_mayak_model import (
    AgentRuntimeUniversalMayakModelAdapter,
)


class CapturingRuntime:
    def __init__(
        self,
        result: AgentRuntimeResult | None = None,
    ) -> None:
        self.commands: list[AgentTurnCommand] = []
        self.result = result or AgentRuntimeResult(
            AgentResultKind.REPLY,
            "mayak_answer",
            candidate_text="Следующий разумный шаг.",
            input_tokens=5,
            output_tokens=4,
        )

    async def propose(self, command: AgentTurnCommand) -> AgentRuntimeResult:
        self.commands.append(command)
        return self.result


def _deployment() -> AgentDeployment:
    revision = InstructionRevision(
        revision_key=UNIVERSAL_MAYAK_PROMPT_REF,
        source_repository="markmalina/Gate",
        source_path="universal_mayak_v1",
        source_commit_sha="a" * 40,
        content_sha256=UNIVERSAL_MAYAK_PROMPT_SHA256,
    )
    return AgentDeployment(
        deployment_key="universal-mayak-v1-openai",
        agent_key=AgentKey.OKO,
        instruction=revision,
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        policy_version="universal-mayak-v1",
        state=AgentDeploymentState.ACTIVE,
    )


def _request() -> UniversalMayakModelRequest:
    scope = DemoScope(
        workspace_ref="11111111-1111-1111-1111-111111111111",
        installation_ref="22222222-2222-2222-2222-222222222222",
        dialog_ref="33333333-3333-3333-3333-333333333333",
    )
    return UniversalMayakModelRequest(
        prompt_ref=UNIVERSAL_MAYAK_PROMPT_REF,
        prompt_sha256=UNIVERSAL_MAYAK_PROMPT_SHA256,
        public_label="Око возрождения",
        user_text="Какой следующий шаг?",
        context=(
            DemoContextMessage(
                scope=scope,
                message_ref="44444444-4444-4444-4444-444444444444",
                role="user",
                text="Только контекст этого диалога.",
                occurred_at=datetime(2026, 7, 25, 12, tzinfo=UTC),
            ),
        ),
    )


def test_adapter_reuses_agent_runtime_with_text_only_context() -> None:
    runtime = CapturingRuntime()
    adapter = AgentRuntimeUniversalMayakModelAdapter(
        runtime=runtime,
        deployment=_deployment(),
    )

    reply = asyncio.run(adapter.propose(_request()))

    assert reply.text == "Следующий разумный шаг."
    assert reply.result_code == "mayak_answer"
    assert len(runtime.commands) == 1
    command = runtime.commands[0]
    assert command.user_text == "Какой следующий шаг?"
    assert [(item.role, item.content) for item in command.context] == [
        ("user", "Только контекст этого диалога."),
    ]
    serialized = repr(command)
    for provider_or_gate_id in (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
    ):
        assert provider_or_gate_id not in serialized


def test_prompt_binding_fails_before_runtime_call() -> None:
    runtime = CapturingRuntime()
    adapter = AgentRuntimeUniversalMayakModelAdapter(
        runtime=runtime,
        deployment=_deployment(),
    )
    request = _request()
    mismatched = UniversalMayakModelRequest(
        prompt_ref=request.prompt_ref,
        prompt_sha256="0" * 64,
        public_label=request.public_label,
        user_text=request.user_text,
        context=request.context,
    )

    with pytest.raises(UniversalMayakDemoError, match="mayak_model_digest_mismatch"):
        asyncio.run(adapter.propose(mismatched))

    assert runtime.commands == []


def test_non_reply_runtime_outcome_fails_closed() -> None:
    runtime = CapturingRuntime(
        AgentRuntimeResult(
            AgentResultKind.HANDOFF,
            "mayak_handoff",
            input_tokens=2,
            output_tokens=1,
        )
    )
    adapter = AgentRuntimeUniversalMayakModelAdapter(
        runtime=runtime,
        deployment=_deployment(),
    )

    with pytest.raises(UniversalMayakDemoError, match="mayak_model_non_reply"):
        asyncio.run(adapter.propose(_request()))
