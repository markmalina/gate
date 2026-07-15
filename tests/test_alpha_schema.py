"""Schema guards for Alpha installation and delivery capabilities."""

import importlib.util
from pathlib import Path

from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def test_alpha_capabilities_are_workspace_scoped_and_target_bound() -> None:
    for model in (AgentInstallationModel, DeliveryGrantModel, AgentSendAttemptModel):
        assert model.__table__.columns["workspace_id"].nullable is False

    assert "fk_agent_installations_connector_workspace" in _constraint_names(AgentInstallationModel)
    assert "fk_agent_installations_dialog_workspace" in _constraint_names(AgentInstallationModel)
    assert "uq_delivery_grants_candidate" in _constraint_names(DeliveryGrantModel)
    assert "uq_agent_send_attempts_grant" in _constraint_names(AgentSendAttemptModel)


def test_agent_send_attempt_is_separate_and_cannot_select_a_target() -> None:
    columns = set(AgentSendAttemptModel.__table__.columns.keys())

    assert "grant_id" in columns
    assert not columns.intersection({"dialog_id", "recipient_id", "candidate_text"})


def test_alpha_migration_is_reversible_and_follows_agent_kernel() -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0018_alpha_delivery.py"
    spec = importlib.util.spec_from_file_location("alpha_delivery_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0018_alpha_delivery"
    assert len(module.revision) <= 32
    assert module.down_revision == "0017_agent_kernel"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
