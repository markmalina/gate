"""Add Relationship CRM identities, explicit attention, notes and work items.

Revision ID: 0029_relationship_crm_personal
Revises: 0028_relationship_crm_core
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_relationship_crm_personal"
down_revision: str | None = "0028_relationship_crm_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _relationship_scope_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["relationship_id", "space_id", "person_id", "workspace_id"],
        [
            "relationship_crm_relationships.id",
            "relationship_crm_relationships.space_id",
            "relationship_crm_relationships.person_id",
            "relationship_crm_relationships.workspace_id",
        ],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "relationship_crm_channel_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_key", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("connector_ref", sa.String(length=128), nullable=False),
        sa.Column("external_subject_ref", sa.String(length=512), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('telegram', 'email', 'phone', 'other')",
            name="ck_relationship_crm_identities_channel",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "workspace_id"],
            ["relationship_crm_people.id", "relationship_crm_people.workspace_id"],
            name="fk_relationship_crm_identities_person_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "identity_key",
            name="uq_relationship_crm_identities_workspace_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "channel",
            "connector_ref",
            "external_subject_ref",
            name="uq_relationship_crm_identities_external",
        ),
    )

    op.create_table(
        "relationship_crm_attention",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_ref", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_key", sa.String(length=128), nullable=False),
        sa.Column("last_command_key", sa.String(length=128), nullable=False),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=2048), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('untriaged', 'action_on_me', 'waiting_on_them', "
            "'scheduled', 'no_action', 'archived')",
            name="ck_relationship_crm_attention_state",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_relationship_crm_attention_revision",
        ),
        sa.CheckConstraint(
            "(state = 'scheduled' AND next_review_at IS NOT NULL) "
            "OR state <> 'scheduled'",
            name="ck_relationship_crm_attention_scheduled_review",
        ),
        _relationship_scope_fk(
            "fk_relationship_crm_attention_relationship_scope"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "conversation_ref",
            name="uq_relationship_crm_attention_conversation",
        ),
    )
    op.create_index(
        "ix_relationship_crm_attention_review",
        "relationship_crm_attention",
        ["workspace_id", "space_id", "state", "next_review_at"],
        unique=False,
    )

    op.create_table(
        "relationship_crm_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("actor_key", sa.String(length=128), nullable=False),
        sa.Column("conversation_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('note', 'thought')",
            name="ck_relationship_crm_notes_kind",
        ),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 16384",
            name="ck_relationship_crm_notes_body",
        ),
        _relationship_scope_fk("fk_relationship_crm_notes_relationship_scope"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "note_key",
            name="uq_relationship_crm_notes_space_key",
        ),
    )
    op.create_index(
        "ix_relationship_crm_notes_person_created",
        "relationship_crm_notes",
        ["workspace_id", "space_id", "person_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "relationship_crm_work_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("responsible_party", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("conversation_ref", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "responsible_party IN ('workspace_owner', 'person', 'shared', 'unassigned')",
            name="ck_relationship_crm_work_items_responsible",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'cancelled')",
            name="ck_relationship_crm_work_items_status",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_relationship_crm_work_items_dates",
        ),
        _relationship_scope_fk(
            "fk_relationship_crm_work_items_relationship_scope"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "space_id",
            "work_item_key",
            name="uq_relationship_crm_work_items_space_key",
        ),
    )
    op.create_index(
        "ix_relationship_crm_work_items_review",
        "relationship_crm_work_items",
        ["workspace_id", "space_id", "status", "next_review_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relationship_crm_work_items_review",
        table_name="relationship_crm_work_items",
    )
    op.drop_table("relationship_crm_work_items")
    op.drop_index(
        "ix_relationship_crm_notes_person_created",
        table_name="relationship_crm_notes",
    )
    op.drop_table("relationship_crm_notes")
    op.drop_index(
        "ix_relationship_crm_attention_review",
        table_name="relationship_crm_attention",
    )
    op.drop_table("relationship_crm_attention")
    op.drop_table("relationship_crm_channel_identities")
