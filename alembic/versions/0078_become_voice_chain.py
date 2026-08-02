"""Add provider-disabled BECOME voice correlation and chain slots.

Revision ID: 0078_become_voice_chain
Revises: 0077_become_media_prompt
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_become_voice_chain"
down_revision: str | None = "0077_become_media_prompt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_voice_correlations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "media_intake_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("correlation_ref", sa.String(length=128), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("intake_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-voice-correlation@1'",
            name="ck_become_voice_correlation_schema",
        ),
        sa.CheckConstraint(
            "correlation_ref LIKE 'voice_corr_%' "
            "AND char_length(intake_digest) = 64",
            name="ck_become_voice_correlation_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_voice_correlation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_intake_id"],
            ["become_media_intakes.id"],
            name="fk_become_voice_correlation_media",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_intake_id",
            name="uq_become_voice_correlation_media",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "correlation_ref",
            name="uq_become_voice_correlation_workspace_ref",
        ),
    )
    op.create_table(
        "become_voice_chain_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "terminal_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
            "schema_name = 'gate.become-chain-slot@1'",
            name="ck_become_voice_chain_slot_schema",
        ),
        sa.CheckConstraint(
            "(stage = 'relations' AND position = 1) OR "
            "(stage = 'mayaki' AND position = 2) OR "
            "(stage = 'oko' AND position = 3) OR "
            "(stage = 'take' AND position = 4)",
            name="ck_become_voice_chain_slot_position",
        ),
        sa.CheckConstraint(
            "state IN "
            "('blocked', 'ready', 'requested', 'completed', 'failed', 'unknown')",
            name="ck_become_voice_chain_slot_state",
        ),
        sa.CheckConstraint(
            "("
            "state IN ('blocked', 'ready') "
            "AND request_digest IS NULL "
            "AND receipt_digest IS NULL "
            "AND terminal_at IS NULL"
            ") OR ("
            "state = 'requested' "
            "AND char_length(request_digest) = 64 "
            "AND receipt_digest IS NULL "
            "AND terminal_at IS NULL"
            ") OR ("
            "state IN ('completed', 'failed', 'unknown') "
            "AND char_length(request_digest) = 64 "
            "AND char_length(receipt_digest) = 64 "
            "AND terminal_at IS NOT NULL"
            ")",
            name="ck_become_voice_chain_slot_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_voice_chain_slot_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            name="fk_become_voice_chain_slot_correlation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correlation_id",
            "stage",
            name="uq_become_voice_chain_slot_stage",
        ),
        sa.UniqueConstraint(
            "correlation_id",
            "position",
            name="uq_become_voice_chain_slot_position",
        ),
    )
    op.create_index(
        "ix_become_voice_chain_workspace_state",
        "become_voice_chain_slots",
        ["workspace_id", "stage", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_voice_chain_workspace_state",
        table_name="become_voice_chain_slots",
    )
    op.drop_table("become_voice_chain_slots")
    op.drop_table("become_voice_correlations")
