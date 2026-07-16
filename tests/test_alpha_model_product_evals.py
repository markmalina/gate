"""Offline product-quality evaluations for Alpha's model contract."""

import asyncio
import json
from pathlib import Path

import httpx
from app.application.agent_kernel import (
    AgentDeployment,
    AgentDeploymentState,
    AgentSession,
    AgentTurnCommand,
)
from app.application.alpha_model_product_evals import (
    ALPHA_MODEL_PRODUCT_CASES,
    evaluate_alpha_product_suite,
)
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.infrastructure.openai_agent_runtime import OpenAIResponsesAgentRuntime


def _bundle():
    return DeployedInstructionBundleLoader(
        Path(__file__).parents[1] / "agent_bundles"
    ).load("alpha_model_v1")


def _deployment(bundle) -> AgentDeployment:
    return AgentDeployment(
        deployment_key="alpha-model-v1-openai",
        agent_key=bundle.agent_key,
        instruction=bundle.revision,
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        policy_version="alpha-model-v1",
        state=AgentDeploymentState.STAGED,
    )


def test_product_suite_covers_voice_clarification_and_boundaries() -> None:
    report = evaluate_alpha_product_suite()

    assert report.passed is True
    assert report.total_cases == 12
    assert report.russian_cases == 11
    assert report.english_cases == 1
    assert report.concise_answer_cases == 4
    assert report.clarification_cases == 3
    assert report.action_boundary_cases == 3
    assert report.human_handoff_cases == 2


def test_product_cases_cross_provider_contract_without_network() -> None:
    bundle = _bundle()
    expected = {
        case.input_text: case.expected for case in ALPHA_MODEL_PRODUCT_CASES
    }
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert payload["tools"] == []
        input_text = payload["input"][-1]["content"]
        result = expected[input_text]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(
                    {
                        "kind": result.kind.value,
                        "result_code": result.result_code,
                        "candidate_text": result.candidate_text,
                    }
                ),
                "usage": {"input_tokens": 90, "output_tokens": 30},
            },
        )

    async def run() -> None:
        deployment = _deployment(bundle)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            runtime = OpenAIResponsesAgentRuntime(
                bundle=bundle,
                http_client=client,
                api_key="test-key",
                model=deployment.model_profile,
                api_base_url="https://api.openai.com/v1",
                max_output_tokens=512,
                timeout_seconds=30,
                data_gate_approved=True,
            )
            for index, case in enumerate(ALPHA_MODEL_PRODUCT_CASES):
                result = await runtime.propose(
                    AgentTurnCommand(
                        turn_key=f"product-eval-turn-{index}",
                        session=AgentSession(
                            session_key=f"product-eval-session-{index}",
                            workspace_id="eval-workspace",
                            dialog_id="eval-dialog",
                            deployment=deployment,
                        ),
                        trigger_message_id=f"product-eval-message-{index}",
                        user_text=case.input_text,
                    )
                )
                assert result.kind is case.expected.kind
                assert result.result_code == case.expected.result_code
                assert result.candidate_text == case.expected.candidate_text

    asyncio.run(run())
    assert calls == len(ALPHA_MODEL_PRODUCT_CASES)
