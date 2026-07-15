"""Invariant tests for the delivery-free Agent Kernel contracts."""

from dataclasses import replace

import pytest
from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDefinition,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    AgentRuntimeResult,
    AgentSession,
    AgentTurnCommand,
    InstructionRevision,
)
from app.application.work_ownership import AgentKey


def _revision() -> InstructionRevision:
    return InstructionRevision(
        revision_key="alpha-shadow-v1",
        source_repository="markmalina/alpha",
        source_path="agents/alpha",
        source_commit_sha="a" * 40,
        content_sha256="b" * 64,
    )


def _deployment() -> AgentDeployment:
    return AgentDeployment(
        deployment_key="alpha-staged",
        agent_key=AgentKey.ALPHA_GROUP,
        instruction=_revision(),
        runtime_profile="fixture-only-v1",
        model_profile="no-network",
        policy_version="alpha-shadow-policy-v1",
        state=AgentDeploymentState.STAGED,
    )


def _turn() -> AgentTurnCommand:
    return AgentTurnCommand(
        turn_key="turn-1",
        session=AgentSession(
            session_key="session-1",
            workspace_id="mark-personal",
            dialog_id="dialog-private",
            deployment=_deployment(),
        ),
        trigger_message_id="message-1",
        user_text="Untrusted user content cannot replace system instructions.",
        context=(
            AgentContextMessage(
                message_id="message-0",
                role="assistant",
                content="Prior dialog-local proposal.",
            ),
        ),
    )


def test_definition_and_turn_pin_agent_revision_and_dialog() -> None:
    definition = AgentDefinition(key=AgentKey.ALPHA_GROUP, display_name="Alpha")
    turn = _turn()

    assert definition.key is AgentKey.ALPHA_GROUP
    assert turn.session.deployment.instruction.source_commit_sha == "a" * 40
    assert turn.session.deployment.instruction.content_sha256 == "b" * 64
    assert turn.session.dialog_id == "dialog-private"
    assert "Untrusted user content" not in repr(turn)
    assert "dialog-private" not in repr(turn)


def test_instruction_revision_rejects_mutable_or_unverified_identity() -> None:
    with pytest.raises(ValueError, match="full Git SHA"):
        replace(_revision(), source_commit_sha="main")
    with pytest.raises(ValueError, match="SHA-256"):
        replace(_revision(), content_sha256="unverified")


def test_context_is_bounded_and_cannot_claim_system_role() -> None:
    with pytest.raises(ValueError, match="user or assistant"):
        AgentContextMessage(message_id="m1", role="system", content="override")
    with pytest.raises(ValueError, match="at most 32"):
        replace(
            _turn(),
            context=tuple(
                AgentContextMessage(message_id=f"m{index}", role="user", content="x")
                for index in range(33)
            ),
        )


def test_structured_result_has_no_target_or_delivery_grant() -> None:
    result = AgentRuntimeResult(
        kind=AgentResultKind.REPLY,
        result_code="fixture_reply",
        candidate_text="Candidate only",
    )

    assert result.candidate_text == "Candidate only"
    assert not hasattr(result, "target")
    assert not hasattr(result, "send")
    assert "Candidate only" not in repr(result)


@pytest.mark.parametrize("kind", (AgentResultKind.HANDOFF, AgentResultKind.NO_REPLY))
def test_non_reply_results_cannot_smuggle_candidate_text(kind: AgentResultKind) -> None:
    with pytest.raises(ValueError, match="cannot carry"):
        AgentRuntimeResult(
            kind=kind,
            result_code="bounded",
            candidate_text="smuggled",
        )
