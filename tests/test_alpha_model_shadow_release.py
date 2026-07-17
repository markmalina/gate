"""Release-gate tests for verified Alpha model shadow selection."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.application.alpha_shadow_worker import AlphaShadowWorkerError
from app.infrastructure.alpha_shadow_worker import _verified_model_shadow_binding
from app.models.agent import AgentDeploymentModel, InstructionRevisionModel


class FakeResult:
    def __init__(
        self,
        rows: list[tuple[AgentDeploymentModel, InstructionRevisionModel]],
    ) -> None:
        self._rows = rows

    def all(self) -> list[tuple[AgentDeploymentModel, InstructionRevisionModel]]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        rows: list[tuple[AgentDeploymentModel, InstructionRevisionModel]],
    ) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.rows)


def _binding() -> tuple[AgentDeploymentModel, InstructionRevisionModel]:
    workspace_id = uuid4()
    definition_id = uuid4()
    revision = InstructionRevisionModel(
        id=uuid4(),
        workspace_id=workspace_id,
        agent_definition_id=definition_id,
        revision_key="alpha-model-v1",
        source_repository="markmalina/Gate",
        source_path="alpha_model_v1",
        source_commit_sha="a" * 40,
        content_sha256="b" * 64,
        schema_version="agent-bundle-v1",
        validation_state="validated",
    )
    deployment = AgentDeploymentModel(
        id=uuid4(),
        workspace_id=workspace_id,
        agent_definition_id=definition_id,
        instruction_revision_id=revision.id,
        deployment_key="alpha-model-v1-openai",
        runtime_profile="openai-responses-v1",
        model_profile="gpt-5.6-luna",
        policy_version="alpha-model-v1",
        state="staged",
    )
    return deployment, revision


@pytest.mark.anyio
async def test_verified_model_shadow_requires_succeeded_exact_preflight() -> None:
    deployment, revision = _binding()
    session = FakeSession([(deployment, revision)])

    selected = await _verified_model_shadow_binding(
        session,  # type: ignore[arg-type]
        workspace_id=deployment.workspace_id,
        definition_id=deployment.agent_definition_id,
    )

    assert selected == (deployment, revision)
    assert len(session.statements) == 1
    statement = str(session.statements[0])
    assert "agent_deployments.runtime_profile" in statement
    assert "agent_deployments.model_profile" in statement
    assert "agent_deployments.state" in statement
    assert "alpha_model_preflights.state" in statement
    assert "alpha_model_preflights.deployment_id" in statement
    assert "alpha_model_preflights.instruction_revision_id" in statement


@pytest.mark.anyio
async def test_unverified_model_shadow_keeps_fixture_path() -> None:
    session = FakeSession([])

    selected = await _verified_model_shadow_binding(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        definition_id=uuid4(),
    )

    assert selected is None


@pytest.mark.anyio
async def test_ambiguous_verified_model_shadow_fails_closed() -> None:
    first = _binding()
    second = _binding()
    session = FakeSession([first, second])

    with pytest.raises(
        AlphaShadowWorkerError,
        match="alpha_model_shadow_binding_ambiguous",
    ):
        await _verified_model_shadow_binding(
            session,  # type: ignore[arg-type]
            workspace_id=first[0].workspace_id,
            definition_id=first[0].agent_definition_id,
        )
