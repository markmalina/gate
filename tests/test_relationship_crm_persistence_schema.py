"""Schema and migration guards for Relationship CRM core persistence."""

import importlib.util
from pathlib import Path

from app.models.relationship_crm import (
    RelationshipInteractionModel,
    RelationshipModel,
    RelationshipPersonModel,
    RelationshipSpaceModel,
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


def test_every_core_record_is_workspace_and_space_scoped() -> None:
    assert ("id", "workspace_id") in _unique_column_sets(RelationshipSpaceModel)
    assert ("id", "workspace_id") in _unique_column_sets(RelationshipPersonModel)
    assert (
        "id",
        "space_id",
        "person_id",
        "workspace_id",
    ) in _unique_column_sets(RelationshipModel)

    assert {
        "fk_relationship_crm_relationships_space_workspace",
        "fk_relationship_crm_relationships_person_workspace",
    }.issubset(_foreign_key_names(RelationshipModel))
    assert {
        "fk_relationship_crm_interactions_relationship_scope",
    }.issubset(_foreign_key_names(RelationshipInteractionModel))


def test_interactions_are_extraction_ready_and_transport_neutral() -> None:
    columns = set(RelationshipInteractionModel.__table__.columns.keys())

    assert {
        "workspace_id",
        "space_id",
        "person_id",
        "relationship_id",
        "interaction_key",
        "kind",
        "direction",
        "occurred_at",
        "source_ref",
    }.issubset(columns)
    assert not columns.intersection(
        {
            "dialog_id",
            "canonical_message_id",
            "telegram_chat_id",
            "reply_task_id",
            "send_attempt_id",
        }
    )
    assert (
        "workspace_id",
        "space_id",
        "source_ref",
    ) in _unique_column_sets(RelationshipInteractionModel)


def test_space_kind_role_and_interaction_taxonomies_are_constrained() -> None:
    assert "ck_relationship_crm_spaces_kind" in _constraint_names(
        RelationshipSpaceModel
    )
    assert "ck_relationship_crm_relationships_role" in _constraint_names(
        RelationshipModel
    )
    assert "ck_relationship_crm_interactions_kind" in _constraint_names(
        RelationshipInteractionModel
    )
    assert "ck_relationship_crm_interactions_direction" in _constraint_names(
        RelationshipInteractionModel
    )


def test_relationship_crm_migration_follows_current_head_and_is_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0028_relationship_crm_core.py"
    )
    spec = importlib.util.spec_from_file_location(
        "relationship_crm_core_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0028_relationship_crm_core"
    assert module.down_revision == "0027_alpha_voice_intake"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_alembic_metadata_registers_relationship_crm_tables() -> None:
    env_source = (
        Path(__file__).parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")

    assert "relationship_crm" in env_source
