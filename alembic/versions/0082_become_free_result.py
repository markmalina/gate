"""Create private BECOME free-result artifact and one-shot delivery.

Revision ID: 0082_become_free_result
Revises: 0081_become_i4_recovery_v2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_become_free_result"
down_revision: str | None = "0081_become_i4_recovery_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_free_result_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "dialog_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "recovery_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "take_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("result_key", sa.String(length=128), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=False),
        sa.Column("transcript_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "take_receipt_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "destination_binding_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "receipt_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-free-result@1'",
            name="ck_become_free_result_schema",
        ),
        sa.CheckConstraint(
            "char_length(result_digest) = 64 "
            "AND char_length(transcript_digest) = 64 "
            "AND char_length(take_receipt_digest) = 64 "
            "AND char_length(destination_binding_digest) = 64 "
            "AND char_length(receipt_digest) = 64",
            name="ck_become_free_result_digests",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_free_result_installation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_become_free_result_dialog_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recovery_attempt_id"],
            ["become_i4_recovery_attempts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["take_event_id"],
            ["become_i4_recovery_stage_events.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correlation_id",
            name="uq_become_free_result_correlation",
        ),
        sa.UniqueConstraint(
            "take_event_id",
            name="uq_become_free_result_take_event",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "result_key",
            name="uq_become_free_result_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_free_result_workspace_created",
        "become_free_result_artifacts",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "become_free_result_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "dialog_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("delivery_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column(
            "destination_binding_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "provider_message_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "receipt_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-free-result-delivery@1'",
            name="ck_become_free_delivery_schema",
        ),
        sa.CheckConstraint(
            "state IN ('sending', 'sent', 'failed', 'blocked', 'unknown')",
            name="ck_become_free_delivery_state",
        ),
        sa.CheckConstraint(
            "max_calls = 1 AND char_length(destination_binding_digest) = 64",
            name="ck_become_free_delivery_boundary",
        ),
        sa.CheckConstraint(
            "(state = 'sending' "
            "AND provider_message_id IS NULL "
            "AND error_code IS NULL "
            "AND receipt_digest IS NULL "
            "AND receipt_payload IS NULL "
            "AND sent_at IS NULL "
            "AND finished_at IS NULL) OR ("
            "state = 'sent' "
            "AND provider_message_id IS NOT NULL "
            "AND error_code IS NULL "
            "AND char_length(receipt_digest) = 64 "
            "AND receipt_payload IS NOT NULL "
            "AND sent_at IS NOT NULL "
            "AND finished_at IS NOT NULL) OR ("
            "state IN ('failed', 'blocked', 'unknown') "
            "AND provider_message_id IS NULL "
            "AND error_code IS NOT NULL "
            "AND char_length(receipt_digest) = 64 "
            "AND receipt_payload IS NOT NULL "
            "AND sent_at IS NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_become_free_delivery_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["become_free_result_artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_free_delivery_installation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_become_free_delivery_dialog_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            name="uq_become_free_delivery_artifact",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "delivery_key",
            name="uq_become_free_delivery_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_free_delivery_workspace_state",
        "become_free_result_deliveries",
        ["workspace_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_free_delivery_workspace_state",
        table_name="become_free_result_deliveries",
    )
    op.drop_table("become_free_result_deliveries")
    op.drop_index(
        "ix_become_free_result_workspace_created",
        table_name="become_free_result_artifacts",
    )
    op.drop_table("become_free_result_artifacts")
