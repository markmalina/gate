"""Safety contract for the dormant OpenAI Alpha runtime."""

import asyncio
import json

import httpx
import pytest
from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    AgentSession,
    AgentTurnCommand,
    InstructionRevision,
)
from app.application.alpha_shadow_worker import AlphaShadowWorkerError
from app.application.instruction_bundles import LoadedInstructionBundle
from app.application.work_ownership import AgentKey
from app.infrastructure.openai_agent_runtime import (
    ConfiguredAlphaRuntimeFactory,
    OpenAIAgentRuntimeError,
    OpenAIResponsesAgentRuntime,
    _model_input,
)


def _bundle(*, capabilities: frozenset[str] | None = None) -> LoadedInstructionBundle:
    revision = InstructionRevision(
        revision_key="alpha-model-v1",
        source_repository="markmalina/Gate",
        source_path="agent_bundles/alpha_model_v1",
        source_commit_sha="a" * 40,
        content_sha256="b" * 64,
    )
    return LoadedInstructionBundle(
        agent_key=AgentKey.ALPHA_GROUP,
        revision=revision,
        system_instructions="You are Alpha, a disclosed digital assistant.",
        allowed_capabilities=capabilities or frozenset({"reply", "handoff", "no_reply"}),
        evaluations=(),
    )


def _deployment(bundle: LoadedInstructionBundle) -> AgentDeployment:
    return AgentDeployment(
        deployment_key="alpha-model-v1",
        agent_key=AgentKey.ALPHA_GROUP,
        instruction=bundle.revision,
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        policy_version="alpha-model-v1",
        state=AgentDeploymentState.STAGED,
    )


def _command(
    bundle: LoadedInstructionBundle,
    *,
    user_text: str = "/alpha Сформулируй короткое приветствие",
) -> AgentTurnCommand:
    deployment = _deployment(bundle)
    return AgentTurnCommand(
        turn_key="turn-private-id",
        session=AgentSession(
            session_key="session-private-id",
            workspace_id="mark-personal",
            dialog_id="dialog-private-id",
            deployment=deployment,
        ),
        trigger_message_id="message-private-id",
        user_text=user_text,
        context=(
            AgentContextMessage(
                message_id="context-private-id",
                role="user",
                content="Контекст только этой группы.",
            ),
        ),
    )


def test_openai_runtime_sends_minimized_non_stored_structured_request() -> None:
    bundle = _bundle()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
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
                "usage": {"input_tokens": 41, "output_tokens": 7},
            },
        )

    async def run() -> object:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model="gpt-5.6-luna",
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=True,
            )
            return await runtime.propose(_command(bundle))

    result = asyncio.run(run())

    assert result.kind is AgentResultKind.REPLY
    assert result.candidate_text == "Здравствуйте!"
    assert result.input_tokens == 41
    assert result.output_tokens == 7
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    payload = json.loads(request.content)
    assert payload["store"] is False
    assert payload["tools"] == []
    assert payload["model"] == "gpt-5.6-luna"
    assert "Gate has already verified that the final user message invokes" in payload[
        "instructions"
    ]
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    serialized = request.content.decode()
    assert "turn-private-id" not in serialized
    assert "session-private-id" not in serialized
    assert "dialog-private-id" not in serialized
    assert "message-private-id" not in serialized
    assert "context-private-id" not in serialized


@pytest.mark.parametrize(
    ("user_text", "expected"),
    [
        ("/beta проверка связи", "проверка связи"),
        ("/beta@BetaViaBot кратко ответь", "кратко ответь"),
        ("/BETA@betaviabot   сохрани регистр запроса", "сохрани регистр запроса"),
        ("/beta", ""),
        (
            "/alpha Сформулируй короткое приветствие",
            "/alpha Сформулируй короткое приветствие",
        ),
        ("обычный текст", "обычный текст"),
    ],
)
def test_model_input_strips_only_beta_transport_command(
    user_text: str,
    expected: str,
) -> None:
    messages = _model_input(_command(_bundle(), user_text=user_text))

    assert messages[-1] == {"role": "user", "content": expected}


def test_model_data_gate_blocks_before_any_provider_call() -> None:
    bundle = _bundle()
    provider_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("closed data gate must not call OpenAI")

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model="gpt-5.6-luna",
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=False,
            )
            with pytest.raises(
                OpenAIAgentRuntimeError,
                match="model_data_gate_not_approved",
            ):
                await runtime.propose(_command(bundle))

    asyncio.run(run())
    assert provider_calls == 0


def test_factory_requires_both_release_gates_for_model_profile() -> None:
    bundle = _bundle()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(500)
            )
        ) as client:
            factory = ConfiguredAlphaRuntimeFactory(
                http_client=client,
                api_key="test-key",
                api_base_url="https://api.openai.com/v1",
                model_runtime_enabled=True,
                model_data_gate_approved=False,
                max_output_tokens=512,
                timeout_seconds=30,
            )
            with pytest.raises(
                AlphaShadowWorkerError,
                match="alpha_model_data_gate_not_approved",
            ):
                factory.build(
                    bundle=bundle,
                    deployment=_deployment(bundle),
                )

    asyncio.run(run())


def test_unapproved_model_capability_fails_closed() -> None:
    bundle = _bundle(capabilities=frozenset({"no_reply"}))

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output_text": json.dumps(
                            {
                                "kind": "reply",
                                "result_code": "not_allowed",
                                "candidate_text": "text",
                            }
                        ),
                    },
                )
            )
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model="gpt-5.6-luna",
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=True,
            )
            with pytest.raises(
                OpenAIAgentRuntimeError,
                match="capability_not_allowed",
            ):
                await runtime.propose(_command(bundle))

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "openai_request_rejected"),
        (401, "openai_credentials_rejected"),
        (403, "openai_credentials_rejected"),
        (429, "openai_rate_limited"),
        (500, "openai_provider_unavailable"),
        (503, "openai_provider_unavailable"),
    ],
)
def test_provider_rejection_is_redacted_terminal_and_never_retried(
    status_code: int,
    expected_code: str,
) -> None:
    bundle = _bundle()
    provider_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return httpx.Response(
            status_code,
            json={"error": {"message": "provider-private-payload"}},
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model="gpt-5.6-luna",
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=True,
            )
            with pytest.raises(OpenAIAgentRuntimeError) as raised:
                await runtime.propose(_command(bundle))
            assert raised.value.code == expected_code
            assert "provider-private-payload" not in str(raised.value)

    asyncio.run(run())
    assert provider_calls == 1


@pytest.mark.parametrize(
    ("usage", "expected_code"),
    [
        (None, "openai_usage_missing"),
        ({}, "openai_usage_invalid"),
        ({"input_tokens": -1, "output_tokens": 3}, "openai_usage_invalid"),
        ({"input_tokens": 3, "output_tokens": True}, "openai_usage_invalid"),
    ],
)
def test_success_without_trustworthy_usage_fails_closed(
    usage: object,
    expected_code: str,
) -> None:
    bundle = _bundle()

    def handler(_request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = {
            "status": "completed",
            "output_text": json.dumps(
                {
                    "kind": "reply",
                    "result_code": "alpha_answer",
                    "candidate_text": "Candidate only",
                }
            ),
        }
        if usage is not None:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model="gpt-5.6-luna",
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=True,
            )
            with pytest.raises(OpenAIAgentRuntimeError) as raised:
                await runtime.propose(_command(bundle))
            assert raised.value.code == expected_code

    asyncio.run(run())
