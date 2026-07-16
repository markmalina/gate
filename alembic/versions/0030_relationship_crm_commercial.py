"""Add Relationship CRM commercial/program pipelines, deals and transitions.

Revision ID: 0030_relationship_crm_commercial
Revises: 0029_relationship_crm_personal
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_relationship_crm_commercial"
down_revision: str | None = "0029_relationship_crm_personal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_relationship_crm_spaces_id_workspace_kind",
        "relationship_crm_spaces",
        ["id", "workspace_id", "kind"],
    )

    op.create_table(
        "relationship_crm_pipelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_kind", sa.String(length=32), nullable=False),
        sa.Column("pipeline_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "space_kind IN ('commercial', 'program')",
            name="ck_relationship_crm_pipelines_space_kind",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_relationship_crm_pipelines_version",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_pipelines_name",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "workspace_id", "space_kind"],
            [
                "relationship_crm_spaces.id",
                "relationship_crm_spaces.workspace_id",
                "relationship_crm_spaces.kind",
            ],
            name="fk_relationship_crm_pipelines_space_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "pipeline_key",
            "version",
            name="uq_relationship_crm_pipelines_version",
        ),
        sa.UniqueConstraint(
            "id",
            "version",
            "space_id",
            "workspace_id",
            name="uq_relationship_crm_pipelines_id_version_scope",
        ),
    )

    op.create_table(
        "relationship_crm_pipeline_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_relationship_crm_stages_position",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_stages_name",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_id", "pipeline_version", "space_id", "workspace_id"],
            [
                "relationship_crm_pipelines.id",
                "relationship_crm_pipelines.version",
                "relationship_crm_pipelines.space_id",
                "relationship_crm_pipelines.workspace_id",
            ],
            name="fk_relationship_crm_stages_pipeline_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_id",
            "stage_key",
            name="uq_relationship_crm_stages_pipeline_key",
        ),
        sa.UniqueConstraint(
            "pipeline_id",
            "position",
            name="uq_relationship_crm_stages_pipeline_position",
        ),
        sa.UniqueConstraint(
            "id",
            "pipeline_id",
            "space_id",
            "workspace_id",
            name="uq_relationship_crm_stages_id_pipeline_scope",
        ),
    )

    op.create_table(
        "relationship_crm_deals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deal_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_key", sa.String(length=128), nullable=False),
        sa.Column("last_command_key", sa.String(length=128), nullable=False),
        sa.Column("amount_microunits", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'won', 'lost', 'cancelled')",
            name="ck_relationship_crm_deals_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_relationship_crm_deals_revision",
        ),
        sa.CheckConstraint(
            "(amount_microunits IS NULL AND currency IS NULL) OR "
            "(amount_microunits >= 0 AND currency ~ '^[A-Z]{3}$')",
            name="ck_relationship_crm_deals_money",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND closed_at IS NULL) OR "
            "(status <> 'open' AND closed_at IS NOT NULL)",
            name="ck_relationship_crm_deals_closure",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND "
            "(closed_at IS NULL OR closed_at >= created_at)",
            name="ck_relationship_crm_deals_dates",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_deals_relationship_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id", "pipeline_id", "space_id", "workspace_id"],
            [
                "relationship_crm_pipeline_stages.id",
                "relationship_crm_pipeline_stages.pipeline_id",
                "relationship_crm_pipeline_stages.space_id",
                "relationship_crm_pipeline_stages.workspace_id",
            ],
            name="fk_relationship_crm_deals_stage_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "deal_key",
            name="uq_relationship_crm_deals_space_key",
        ),
        sa.UniqueConstraint(
            "id",
            "space_id",
            "workspace_id",
            name="uq_relationship_crm_deals_id_scope",
        ),
    )
    op.create_index(
        "ix_relationship_crm_deals_stage",
        "relationship_crm_deals",
        ["workspace_id", "space_id", "status", "stage_id"],
        unique=False,
    )

    op.create_table(
        "relationship_crm_deal_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_key", sa.String(length=128), nullable=False),
        sa.Column("from_stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resulting_status", sa.String(length=32), nullable=False),
        sa.Column("actor_key", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resulting_status IN ('open', 'won', 'lost', 'cancelled')",
            name="ck_relationship_crm_transitions_status",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id", "space_id", "workspace_id"],
            [
                "relationship_crm_deals.id",
                "relationship_crm_deals.space_id",
                "relationship_crm_deals.workspace_id",
            ],
            name="fk_relationship_crm_transitions_deal_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_stage_id", "pipeline_id", "space_id", "workspace_id"],
            [
                "relationship_crm_pipeline_stages.id",
                "relationship_crm_pipeline_stages.pipeline_id",
                "relationship_crm_pipeline_stages.space_id",
                "relationship_crm_pipeline_stages.workspace_id",
            ],
            name="fk_relationship_crm_transitions_from_stage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_stage_id", "pipeline_id", "space_id", "workspace_id"],
            [
                "relationship_crm_pipeline_stages.id",
                "relationship_crm_pipeline_stages.pipeline_id",
                "relationship_crm_pipeline_stages.space_id",
                "relationship_crm_pipeline_stages.workspace_id",
            ],
            name="fk_relationship_crm_transitions_to_stage",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "command_key",
            name="uq_relationship_crm_transitions_command",
        ),
    )
    op.create_index(
        "ix_relationship_crm_transitions_deal_time",
        "relationship_crm_deal_transitions",
        ["workspace_id", "space_id", "deal_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relationship_crm_transitions_deal_time",
        table_name="relationship_crm_deal_transitions",
    )
    op.drop_table("relationship_crm_deal_transitions")
    op.drop_index(
        "ix_relationship_crm_deals_stage",
        table_name="relationship_crm_deals",
    )
    op.drop_table("relationship_crm_deals")
    op.drop_table("relationship_crm_pipeline_stages")
    op.drop_table("relationship_crm_pipelines")
    op.drop_constraint(
        "uq_relationship_crm_spaces_id_workspace_kind",
        "relationship_crm_spaces",
        type_="unique",
    )
