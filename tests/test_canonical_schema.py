"""Schema guards for the tenant-ready canonical messaging foundation."""

import importlib.util
from pathlib import Path

from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    WorkOwnership,
    Workspace,
)


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def test_every_new_aggregate_root_is_explicitly_workspace_scoped() -> None:
    assert "workspace_id" not in Workspace.__table__.columns
    for model in (TransportConnector, Dialog, CanonicalMessage, WorkOwnership):
        assert model.__table__.columns["workspace_id"].nullable is False


def test_dialog_identity_is_connector_scoped_and_project_independent() -> None:
    assert "uq_dialogs_connector_provider" in _constraint_names(Dialog)
    assert "fk_dialogs_connector_workspace" in _constraint_names(Dialog)
    assert "project_id" not in Dialog.__table__.columns


def test_message_and_ownership_idempotency_constraints_exist() -> None:
    assert "uq_canonical_messages_receipt_id" in _constraint_names(CanonicalMessage)
    assert "uq_canonical_messages_dialog_provider" in _constraint_names(CanonicalMessage)
    assert "uq_work_ownerships_dialog" in _constraint_names(WorkOwnership)
    assert "fk_work_ownerships_message_workspace" in _constraint_names(WorkOwnership)


def test_expand_migration_is_reversible_and_follows_current_head() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0016_canonical_messaging_foundation.py"
    )
    spec = importlib.util.spec_from_file_location("canonical_messaging_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0016_canonical_foundation"
    assert len(module.revision) <= 32
    assert module.down_revision == "0015_telegram_queue_incidents"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
