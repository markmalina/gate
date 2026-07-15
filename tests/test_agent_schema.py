"""Schema guards for workspace-scoped Agent Kernel persistence."""

import importlib.util
from pathlib import Path

from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def test_every_agent_record_is_workspace_scoped() -> None:
    for model in (
        AgentDefinitionModel,
        InstructionRevisionModel,
        AgentDeploymentModel,
        AgentSessionModel,
        AgentTurnModel,
    ):
        assert model.__table__.columns["workspace_id"].nullable is False


def test_revision_deployment_session_and_turn_are_immutably_linked() -> None:
    assert "fk_instruction_revisions_agent_workspace" in _constraint_names(InstructionRevisionModel)
    assert "fk_agent_deployments_revision_workspace" in _constraint_names(AgentDeploymentModel)
    assert "fk_agent_sessions_revision_workspace" in _constraint_names(AgentSessionModel)
    assert "fk_agent_turns_revision_workspace" in _constraint_names(AgentTurnModel)
    assert "uq_agent_turns_trigger" in _constraint_names(AgentTurnModel)


def test_turn_has_no_transport_target_or_delivery_authority() -> None:
    columns = set(AgentTurnModel.__table__.columns.keys())

    assert "candidate_text" in columns
    assert not columns.intersection(
        {"connector_id", "provider_dialog_id", "recipient_id", "delivery_grant_id"}
    )


def test_agent_migration_is_reversible_and_follows_canonical_head() -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0017_agent_kernel.py"
    spec = importlib.util.spec_from_file_location("agent_kernel_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0017_agent_kernel"
    assert len(module.revision) <= 32
    assert module.down_revision == "0016_canonical_foundation"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
