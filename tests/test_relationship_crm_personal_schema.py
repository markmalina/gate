"""Guards for Relationship CRM identities and personal workflow persistence."""

import importlib.util
from pathlib import Path

from app.models.relationship_crm import (
    RelationshipAttentionModel,
    RelationshipChannelIdentityModel,
    RelationshipNoteModel,
    RelationshipWorkItemModel,
)
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def _unique_column_sets(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_channel_identity_is_person_scoped_and_not_a_person_primary_key() -> None:
    columns = set(RelationshipChannelIdentityModel.__table__.columns.keys())

    assert {
        "person_id",
        "identity_key",
        "channel",
        "connector_ref",
        "external_subject_ref",
    }.issubset(columns)
    assert "fk_relationship_crm_identities_person_workspace" in _foreign_key_names(
        RelationshipChannelIdentityModel
    )
    assert (
        "workspace_id",
        "channel",
        "connector_ref",
        "external_subject_ref",
    ) in _unique_column_sets(RelationshipChannelIdentityModel)


def test_attention_has_no_answered_or_direction_derived_state() -> None:
    names = _constraint_names(RelationshipAttentionModel)
    columns = set(RelationshipAttentionModel.__table__.columns.keys())

    assert "ck_relationship_crm_attention_state" in names
    assert "ck_relationship_crm_attention_revision" in names
    assert "ck_relationship_crm_attention_scheduled_review" in names
    assert "direction" not in columns
    assert "answered" not in columns
    assert "unanswered" not in columns


def test_notes_and_work_items_are_independent_of_reply_tasks() -> None:
    note_columns = set(RelationshipNoteModel.__table__.columns.keys())
    work_columns = set(RelationshipWorkItemModel.__table__.columns.keys())

    assert {"kind", "body", "actor_key"}.issubset(note_columns)
    assert {
        "title",
        "responsible_party",
        "status",
        "next_review_at",
    }.issubset(work_columns)
    assert "reply_task_id" not in note_columns
    assert "reply_task_id" not in work_columns
    assert "send_attempt_id" not in note_columns
    assert "send_attempt_id" not in work_columns


def test_personal_workflow_tables_share_relationship_scope_foreign_key() -> None:
    assert "fk_relationship_crm_attention_relationship_scope" in _foreign_key_names(
        RelationshipAttentionModel
    )
    assert "fk_relationship_crm_notes_relationship_scope" in _foreign_key_names(
        RelationshipNoteModel
    )
    assert "fk_relationship_crm_work_items_relationship_scope" in _foreign_key_names(
        RelationshipWorkItemModel
    )


def test_personal_workflow_migration_is_linear_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0029_relationship_crm_personal.py"
    )
    spec = importlib.util.spec_from_file_location(
        "relationship_crm_personal_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0029_relationship_crm_personal"
    assert module.down_revision == "0028_relationship_crm_core"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
