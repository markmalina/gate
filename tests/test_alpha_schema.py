"""Schema guards for Alpha installation and delivery capabilities."""

import importlib.util
from pathlib import Path

from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    AlphaUsageRecordModel,
    DeliveryGrantModel,
)


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def test_alpha_capabilities_are_workspace_scoped_and_target_bound() -> None:
    for model in (
        AgentInstallationModel,
        AlphaUsageRecordModel,
        DeliveryGrantModel,
        AgentSendAttemptModel,
    ):
        assert model.__table__.columns["workspace_id"].nullable is False

    assert "fk_agent_installations_connector_workspace" in _constraint_names(AgentInstallationModel)
    assert "fk_agent_installations_dialog_workspace" in _constraint_names(AgentInstallationModel)
    assert "ck_agent_installations_active_binding" in _constraint_names(AgentInstallationModel)
    assert "fk_alpha_usage_trigger_workspace" in _constraint_names(AlphaUsageRecordModel)
    assert "uq_alpha_usage_trigger" in _constraint_names(AlphaUsageRecordModel)
    assert "uq_delivery_grants_candidate" in _constraint_names(DeliveryGrantModel)
    assert "uq_agent_send_attempts_grant" in _constraint_names(AgentSendAttemptModel)


def test_pending_installation_has_no_delivery_or_runtime_binding() -> None:
    assert AgentInstallationModel.__table__.columns["deployment_id"].nullable is True
    assert AgentInstallationModel.__table__.columns["instruction_revision_id"].nullable is True
    columns = set(AlphaUsageRecordModel.__table__.columns.keys())
    assert "trigger_message_id" in columns
    assert not columns.intersection({"candidate_text", "recipient_id", "grant_id"})


def test_agent_send_attempt_is_separate_and_cannot_select_a_target() -> None:
    columns = set(AgentSendAttemptModel.__table__.columns.keys())

    assert "grant_id" in columns
    assert not columns.intersection({"dialog_id", "recipient_id", "candidate_text"})


def test_alpha_lifecycle_migration_is_reversible_and_follows_delivery_schema() -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0019_alpha_lifecycle.py"
    spec = importlib.util.spec_from_file_location("alpha_lifecycle_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0019_alpha_lifecycle"
    assert len(module.revision) <= 32
    assert module.down_revision == "0018_alpha_delivery"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_alpha_canary_release_requires_every_owner_flag_and_exact_artifact() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0020_alpha_canary_release.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_canary_release_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0020_alpha_canary_release"
    assert module.down_revision == "0019_alpha_lifecycle"
    assert len(module._REQUIRED_OWNER_FLAGS) == 6
    assert all(module._enabled(value) for value in ("1", "true", "YES", " on "))
    assert not any(module._enabled(value) for value in (None, "", "0", "false"))
    assert module._REVISION_KEY == "alpha-canary-v1"
    assert module._SOURCE_COMMIT_SHA == "0b5295606694c537c2b59915d0c91e789f1cbb51"
    assert module._CONTENT_SHA256 == (
        "8a40570ed64fe29ba56b50975113a89bee5a59c22c4bf9cc56cf0abf75eba578"
    )
