"""Add module-owned Relationship CRM spaces, people and interaction facts.

Revision ID: 0028_relationship_crm_core
Revises: 0027_alpha_voice_intake
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_relationship_crm_core"
down_revision: str | None = "0027_alpha_voice_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relationship_crm_spaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('personal', 'commercial', 'program')",
            name="ck_relationship_crm_spaces_kind",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_spaces_name",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_relationship_crm_spaces_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_key",
            name="uq_relationship_crm_spaces_workspace_key",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_relationship_crm_spaces_id_workspace",
        ),
    )
    op.create_index(
        "ix_relationship_crm_spaces_workspace_kind",
        "relationship_crm_spaces",
        ["workspace_id", "kind"],
        unique=False,
    )

    op.create_table(
        "relationship_crm_people",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_people_display_name",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_relationship_crm_people_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "person_key",
            name="uq_relationship_crm_people_workspace_key",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_relationship_crm_people_id_workspace",
        ),
    )

    op.create_table(
        "relationship_crm_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_key", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('personal_contact', 'family', 'friend', 'lead', 'customer', "
            "'partner', 'contractor', 'team_member', 'participant', "
            "'program_member', 'mentor', 'other')",
            name="ck_relationship_crm_relationships_role",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= created_at",
            name="ck_relationship_crm_relationships_dates",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "workspace_id"],
            ["relationship_crm_spaces.id", "relationship_crm_spaces.workspace_id"],
            name="fk_relationship_crm_relationships_space_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "workspace_id"],
            ["relationship_crm_people.id", "relationship_crm_people.workspace_id"],
            name="fk_relationship_crm_relationships_person_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "relationship_key",
            name="uq_relationship_crm_relationships_space_key",
        ),
        sa.UniqueConstraint(
            "id",
            "space_id",
            "person_id",
            "workspace_id",
            name="uq_relationship_crm_relationships_id_scope",
        ),
    )
    op.create_index(
        "ix_relationship_crm_relationships_space_role",
        "relationship_crm_relationships",
        ["workspace_id", "space_id", "role"],
        unique=False,
    )

    op.create_table(
        "relationship_crm_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interaction_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('message', 'call', 'meeting', 'payment', 'agreement', "
            "'program_event', 'other')",
            name="ck_relationship_crm_interactions_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_relationship_crm_interactions_direction",
        ),
        sa.CheckConstraint(
            "summary IS NULL OR char_length(summary) BETWEEN 1 AND 4096",
            name="ck_relationship_crm_interactions_summary",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_interactions_relationship_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "interaction_key",
            name="uq_relationship_crm_interactions_space_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "source_ref",
            name="uq_relationship_crm_interactions_source",
        ),
    )
    op.create_index(
        "ix_relationship_crm_interactions_timeline",
        "relationship_crm_interactions",
        ["workspace_id", "space_id", "person_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relationship_crm_interactions_timeline",
        table_name="relationship_crm_interactions",
    )
    op.drop_table("relationship_crm_interactions")
    op.drop_index(
        "ix_relationship_crm_relationships_space_role",
        table_name="relationship_crm_relationships",
    )
    op.drop_table("relationship_crm_relationships")
    op.drop_table("relationship_crm_people")
    op.drop_index(
        "ix_relationship_crm_spaces_workspace_kind",
        table_name="relationship_crm_spaces",
    )
    op.drop_table("relationship_crm_spaces")
