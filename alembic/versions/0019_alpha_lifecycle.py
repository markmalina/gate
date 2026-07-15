"""Add Alpha operator lifecycle and usage accounting.

Revision ID: 0019_alpha_lifecycle
Revises: 0018_alpha_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_alpha_lifecycle"
down_revision: str | None = "0018_alpha_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Permit pending discovery and add idempotent usage reservations."""

    op.alter_column(
        "agent_installations",
        "deployment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "agent_installations",
        "instruction_revision_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "agent_installations",
        sa.Column("dialog_label", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE agent_installations "
        "SET dialog_label = 'Existing approved group' "
        "WHERE dialog_label IS NULL"
    )
    op.alter_column("agent_installations", "dialog_label", nullable=False)
    op.add_column(
        "agent_installations",
        sa.Column("rate_window_seconds", sa.Integer(), server_default="60", nullable=False),
    )
    op.create_check_constraint(
        "ck_agent_installations_active_binding",
        "agent_installations",
        "state NOT IN ('active', 'paused') OR "
        "(deployment_id IS NOT NULL AND instruction_revision_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_agent_installations_rate_window",
        "agent_installations",
        "rate_window_seconds > 0",
    )

    op.create_table(
        "alpha_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usage_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="reserved", nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'recorded', 'released')",
            name="ck_alpha_usage_state",
        ),
        sa.CheckConstraint("reserved_tokens > 0", name="ck_alpha_usage_reserved_tokens"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_alpha_usage_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_alpha_usage_output_tokens",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_alpha_usage_installation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_alpha_usage_trigger_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_alpha_usage_id_workspace"),
        sa.UniqueConstraint("workspace_id", "usage_key", name="uq_alpha_usage_key"),
        sa.UniqueConstraint(
            "installation_id", "trigger_message_id", name="uq_alpha_usage_trigger"
        ),
    )
    op.create_index(
        "ix_alpha_usage_installation_created",
        "alpha_usage_records",
        ["installation_id", "created_at"],
    )


def downgrade() -> None:
    """Remove lifecycle accounting and restore required approved bindings."""

    op.drop_index("ix_alpha_usage_installation_created", table_name="alpha_usage_records")
    op.drop_table("alpha_usage_records")
    op.drop_constraint(
        "ck_agent_installations_rate_window",
        "agent_installations",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_installations_active_binding",
        "agent_installations",
        type_="check",
    )
    op.drop_column("agent_installations", "rate_window_seconds")
    op.drop_column("agent_installations", "dialog_label")
    op.execute(
        "DELETE FROM agent_installations "
        "WHERE deployment_id IS NULL OR instruction_revision_id IS NULL"
    )
    op.alter_column(
        "agent_installations",
        "instruction_revision_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "agent_installations",
        "deployment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
