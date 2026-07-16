"""Schema guards for Relationship CRM commercial and program workflow."""

import importlib.util
from pathlib import Path

from app.models.relationship_crm import (
    RelationshipDealModel,
    RelationshipDealTransitionModel,
    RelationshipPipelineModel,
    RelationshipPipelineStageModel,
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


def test_pipeline_fk_requires_commercial_or_program_space_kind() -> None:
    assert (
        "id",
        "workspace_id",
        "kind",
    ) in _unique_column_sets(RelationshipSpaceModel)
    assert "fk_relationship_crm_pipelines_space_kind" in _foreign_key_names(
        RelationshipPipelineModel
    )
    assert "ck_relationship_crm_pipelines_space_kind" in _constraint_names(
        RelationshipPipelineModel
    )


def test_stages_are_versioned_and_pipeline_scoped() -> None:
    assert "fk_relationship_crm_stages_pipeline_version" in _foreign_key_names(
        RelationshipPipelineStageModel
    )
    assert (
        "pipeline_id",
        "stage_key",
    ) in _unique_column_sets(RelationshipPipelineStageModel)
    assert (
        "pipeline_id",
        "position",
    ) in _unique_column_sets(RelationshipPipelineStageModel)


def test_deal_is_bound_to_relationship_and_valid_pipeline_stage() -> None:
    foreign_keys = _foreign_key_names(RelationshipDealModel)
    columns = set(RelationshipDealModel.__table__.columns.keys())

    assert {
        "fk_relationship_crm_deals_relationship_scope",
        "fk_relationship_crm_deals_stage_scope",
    }.issubset(foreign_keys)
    assert {
        "status",
        "revision",
        "actor_key",
        "last_command_key",
        "amount_microunits",
        "currency",
    }.issubset(columns)
    assert "ck_relationship_crm_deals_closure" in _constraint_names(
        RelationshipDealModel
    )


def test_transition_history_is_actor_attributed_and_idempotent() -> None:
    columns = set(RelationshipDealTransitionModel.__table__.columns.keys())

    assert {
        "command_key",
        "from_stage_id",
        "to_stage_id",
        "resulting_status",
        "actor_key",
        "occurred_at",
    }.issubset(columns)
    assert (
        "workspace_id",
        "space_id",
        "command_key",
    ) in _unique_column_sets(RelationshipDealTransitionModel)
    assert {
        "fk_relationship_crm_transitions_deal_scope",
        "fk_relationship_crm_transitions_from_stage",
        "fk_relationship_crm_transitions_to_stage",
    }.issubset(_foreign_key_names(RelationshipDealTransitionModel))


def test_commercial_migration_is_linear_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0030_relationship_crm_commercial.py"
    )
    spec = importlib.util.spec_from_file_location(
        "relationship_crm_commercial_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0030_relationship_crm_commercial"
    assert module.down_revision == "0029_relationship_crm_personal"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
