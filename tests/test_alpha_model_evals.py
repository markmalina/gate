"""Offline release evaluations for the reviewed Alpha model bundle."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from app.application.agent_kernel import (
    AgentDeployment,
    AgentDeploymentState,
    AgentSession,
    AgentTurnCommand,
)
from app.application.alpha_model_evals import (
    AlphaModelEvalError,
    evaluate_alpha_model_bundle,
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


def test_reviewed_bundle_passes_complete_product_and_safety_coverage() -> None:
    report = evaluate_alpha_model_bundle(_bundle())

    assert report.passed is True
    assert report.total_cases == 8
    assert report.reply_cases == 5
    assert report.handoff_cases == 2
    assert report.no_reply_cases == 1
    assert report.required_result_codes_covered is True


def test_missing_boundary_case_blocks_release_evidence() -> None:
    bundle = _bundle()
    shortened = replace(bundle, evaluations=bundle.evaluations[:-1])

    with pytest.raises(AlphaModelEvalError, match="case_count_invalid"):
        evaluate_alpha_model_bundle(shortened)


def test_all_reviewed_cases_cross_the_provider_contract_without_network() -> None:
    bundle = _bundle()
    expected = {
        fixture.input_text: fixture.result
        for fixture in bundle.evaluations
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
                "usage": {"input_tokens": 100, "output_tokens": 20},
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
            for index, fixture in enumerate(bundle.evaluations):
                result = await runtime.propose(
                    AgentTurnCommand(
                        turn_key=f"eval-turn-{index}",
                        session=AgentSession(
                            session_key=f"eval-session-{index}",
                            workspace_id="eval-workspace",
                            dialog_id="eval-dialog",
                            deployment=deployment,
                        ),
                        trigger_message_id=f"eval-message-{index}",
                        user_text=fixture.input_text,
                    )
                )
                assert result.kind is fixture.result.kind
                assert result.result_code == fixture.result.result_code
                assert result.candidate_text == fixture.result.candidate_text
                assert result.input_tokens == 100
                assert result.output_tokens == 20

    asyncio.run(run())
    assert calls == len(bundle.evaluations)
