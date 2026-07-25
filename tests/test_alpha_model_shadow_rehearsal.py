"""Composed delivery-free rehearsal of the dormant Alpha model shadow path."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
)
from app.application.agent_shadow import AgentShadowProposal
from app.application.alpha_group import AlphaInstallation, AlphaInstallationState
from app.application.alpha_lifecycle import AlphaAccessReason, AlphaUsageReservation
from app.application.alpha_shadow_worker import (
    AlphaProposalDeliveryMode,
    AlphaShadowDisposition,
    AlphaShadowPreparedTurn,
    AlphaShadowWorkerError,
    AlphaTurnSnapshot,
    AlphaTurnStatus,
    ProcessAlphaShadowMessageCommand,
    ProcessAlphaShadowMessageService,
)
from app.application.canonical_messaging import DialogKind
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.infrastructure.openai_agent_runtime import ConfiguredAlphaRuntimeFactory


def _bundle():
    return DeployedInstructionBundleLoader(
        Path(__file__).parents[1] / "agent_bundles"
    ).load("alpha_model_v1")


def _prepared(*, text: str) -> AlphaShadowPreparedTurn:
    bundle = _bundle()
    deployment = AgentDeployment(
        deployment_key="alpha-model-v1-openai",
        agent_key=bundle.agent_key,
        instruction=bundle.revision,
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        policy_version="alpha-model-v1",
        state=AgentDeploymentState.STAGED,
    )
    installation = AlphaInstallation(
        installation_key="installation-1",
        workspace_id="00000000-0000-0000-0000-000000000001",
        dialog_id="00000000-0000-0000-0000-000000000002",
        dialog_kind=DialogKind.SUPERGROUP,
        deployment_key=deployment.deployment_key,
        instruction_content_sha256=bundle.revision.content_sha256,
        state=AlphaInstallationState.ACTIVE,
    )
    return AlphaShadowPreparedTurn(
        workspace_key="mark-personal",
        installation=installation,
        deployment=deployment,
        instruction_bundle_path="alpha_model_v1",
        trigger_message_id="00000000-0000-0000-0000-000000000003",
        user_text=text,
        sender_is_human=True,
        reply_to_bot=False,
        context=(
            AgentContextMessage(
                message_id="00000000-0000-0000-0000-000000000004",
                role="user",
                content="Контекст только этой утверждённой группы.",
            ),
        ),
        max_tokens_per_turn=512,
    )


class MemoryModelShadowPort:
    def __init__(
        self,
        prepared: AlphaShadowPreparedTurn,
        *,
        prepare_error: AlphaShadowWorkerError | None = None,
    ) -> None:
        self.prepared = prepared
        self.prepare_error = prepare_error
        self.turn: AlphaTurnSnapshot | None = None
        self.usage_calls = 0
        self.complete_calls = 0
        self.fail_calls = 0

    async def prepare(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowPreparedTurn:
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.prepared

    async def reserve_usage(
        self,
        prepared: AlphaShadowPreparedTurn,
        *,
        now: datetime,
    ) -> AlphaUsageReservation:
        self.usage_calls += 1
        return AlphaUsageReservation(
            True,
            AlphaAccessReason.ALLOWED,
            usage_key="model-usage-1",
            idempotent_replay=self.usage_calls > 1,
        )

    async def reserve_turn(
        self,
        prepared: AlphaShadowPreparedTurn,
    ) -> AlphaTurnSnapshot:
        if self.turn is None:
            self.turn = AlphaTurnSnapshot(
                turn_key="model-turn-1",
                session_key="model-session-1",
                status=AlphaTurnStatus.QUEUED,
            )
        return self.turn

    async def claim_turn(self, turn_key: str) -> bool:
        if self.turn is None or self.turn.status is not AlphaTurnStatus.QUEUED:
            return False
        self.turn = replace(self.turn, status=AlphaTurnStatus.RUNNING)
        return True

    async def complete_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        proposal: AgentShadowProposal,
    ) -> AlphaTurnSnapshot:
        assert self.turn is not None
        self.complete_calls += 1
        self.turn = AlphaTurnSnapshot(
            turn_key=turn_key,
            session_key=self.turn.session_key,
            status=AlphaTurnStatus.PROPOSED,
            result_kind=proposal.result_kind,
            result_code=proposal.result_code,
            candidate_sha256=proposal.candidate_sha256,
        )
        return self.turn

    async def fail_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        error_code: str,
    ) -> AlphaTurnSnapshot:
        assert self.turn is not None
        self.fail_calls += 1
        self.turn = replace(
            self.turn,
            status=AlphaTurnStatus.FAILED,
            result_kind=None,
            result_code=error_code,
            candidate_sha256=None,
        )
        return self.turn

    async def get_turn(self, turn_key: str) -> AlphaTurnSnapshot:
        assert self.turn is not None
        return self.turn


def _command() -> ProcessAlphaShadowMessageCommand:
    return ProcessAlphaShadowMessageCommand(
        workspace_key="mark-personal",
        trigger_message_id="00000000-0000-0000-0000-000000000003",
        bot_username="AlphaViaBot",
        now=datetime(2026, 7, 15, tzinfo=UTC),
    )


async def _execute(
    port: MemoryModelShadowPort,
    handler,
):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        factory = ConfiguredAlphaRuntimeFactory(
            http_client=client,
            api_key="test-key",
            api_base_url="https://api.openai.com/v1",
            model_runtime_enabled=True,
            model_data_gate_approved=True,
            max_output_tokens=512,
            timeout_seconds=30,
        )
        service = ProcessAlphaShadowMessageService(
            port=port,
            bundle_loader=DeployedInstructionBundleLoader(
                Path(__file__).parents[1] / "agent_bundles"
            ),
            runtime_factory=factory,
        )
        first = await service.execute(_command())
        duplicate = await service.execute(_command())
        return first, duplicate


def test_explicit_command_calls_mock_provider_once_and_never_delivers() -> None:
    port = MemoryModelShadowPort(
        _prepared(text="/alpha@AlphaViaBot Сформулируй короткое приветствие")
    )
    provider_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        payload = json.loads(request.content)
        serialized = request.content.decode()
        assert payload["store"] is False
        assert payload["tools"] == []
        assert payload["input"] == [
            {
                "role": "user",
                "content": "/alpha@AlphaViaBot Сформулируй короткое приветствие",
            }
        ]
        assert "Контекст только этой утверждённой группы." not in serialized
        assert "00000000-0000-0000-0000-000000000002" not in serialized
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(
                    {
                        "kind": "reply",
                        "result_code": "alpha_answer",
                        "candidate_text": "Здравствуйте!",
                    }
                ),
                "usage": {"input_tokens": 90, "output_tokens": 10},
            },
        )

    first, duplicate = asyncio.run(_execute(port, handler))

    assert provider_calls == 1
    assert port.complete_calls == 1
    assert port.fail_calls == 0
    assert first.disposition is AlphaShadowDisposition.PROPOSED
    assert first.result_kind is AgentResultKind.REPLY
    assert first.delivery_mode is AlphaProposalDeliveryMode.SHADOW_ONLY
    assert duplicate.disposition is AlphaShadowDisposition.REPLAYED
    assert duplicate.candidate_sha256 == first.candidate_sha256
    assert first.candidate_sha256 is not None
    assert not hasattr(first, "target")
    assert not hasattr(first, "delivery_grant")


@pytest.mark.parametrize(
    "text",
    [
        "обычное сообщение группы",
        "/alpha@OtherBot это не вызов Alpha",
        "mail@AlphaViaBot.example is not a mention",
    ],
)
def test_unaddressed_or_spoofed_text_never_reaches_provider(text: str) -> None:
    port = MemoryModelShadowPort(_prepared(text=text))
    provider_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("uninvoked text must not call provider")

    first, duplicate = asyncio.run(_execute(port, handler))

    assert first.disposition is AlphaShadowDisposition.NOT_INVOKED
    assert duplicate.disposition is AlphaShadowDisposition.NOT_INVOKED
    assert provider_calls == 0
    assert port.usage_calls == 0
    assert port.turn is None


def test_unknown_group_fails_before_usage_and_provider() -> None:
    port = MemoryModelShadowPort(
        _prepared(text="/alpha@AlphaViaBot hello"),
        prepare_error=AlphaShadowWorkerError("alpha_trigger_not_eligible"),
    )
    provider_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("unknown group must not call provider")

    with pytest.raises(AlphaShadowWorkerError, match="not_eligible"):
        asyncio.run(_execute(port, handler))

    assert provider_calls == 0
    assert port.usage_calls == 0


@pytest.mark.parametrize("failure", ["timeout", "unavailable"])
def test_provider_failure_is_terminal_and_duplicate_never_retries(failure: str) -> None:
    port = MemoryModelShadowPort(_prepared(text="/alpha@AlphaViaBot hello"))
    provider_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        return httpx.Response(503, json={"private": "must-not-leak"})

    first, duplicate = asyncio.run(_execute(port, handler))

    assert provider_calls == 1
    assert port.complete_calls == 0
    assert port.fail_calls == 1
    assert first.disposition is AlphaShadowDisposition.FAILED
    assert first.code == "alpha_runtime_failed"
    assert duplicate.disposition is AlphaShadowDisposition.REPLAYED
    assert duplicate.code == "alpha_runtime_failed"
    assert "private" not in first.code


def test_context_and_input_are_bounded_before_runtime() -> None:
    prepared = _prepared(text="/alpha@AlphaViaBot hello")
    oversized_context = tuple(
        replace(prepared.context[0], message_id=f"message-{index}")
        for index in range(33)
    )

    with pytest.raises(ValueError, match="at most 32 messages"):
        replace(prepared, context=oversized_context)
    with pytest.raises(ValueError, match="bounded non-empty text"):
        replace(prepared, user_text="/alpha " + "x" * 16_000)
