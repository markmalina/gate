"""Add Alpha installation and delivery capability persistence.

Revision ID: 0018_alpha_delivery
Revises: 0017_agent_kernel
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_alpha_delivery"
down_revision: str | None = "0017_agent_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add isolated Alpha state without seeding or activating an installation."""

    op.create_table(
        "agent_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instruction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="discovered", nullable=False),
        sa.Column("max_turns_per_window", sa.Integer(), server_default="5", nullable=False),
        sa.Column("max_tokens_per_turn", sa.Integer(), server_default="2048", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('discovered', 'pending_approval', 'active', 'paused', 'revoked')",
            name="ck_agent_installations_state",
        ),
        sa.CheckConstraint("max_turns_per_window > 0", name="ck_agent_installations_turn_budget"),
        sa.CheckConstraint("max_tokens_per_turn > 0", name="ck_agent_installations_token_budget"),
        sa.ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_agent_installations_connector_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_agent_installations_deployment_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_agent_installations_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_installations_revision_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agent_installations_id_workspace"),
        sa.UniqueConstraint(
            "workspace_id", "connector_id", "dialog_id", name="uq_agent_installations_dialog"
        ),
        sa.UniqueConstraint("workspace_id", "installation_key", name="uq_agent_installations_key"),
    )
    op.create_table(
        "delivery_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_key", sa.String(length=128), nullable=False),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(candidate_sha256) = 64", name="ck_delivery_grants_candidate_sha"
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_delivery_grants_installation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_delivery_grants_trigger_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_delivery_grants_id_workspace"),
        sa.UniqueConstraint(
            "installation_id",
            "trigger_message_id",
            "candidate_sha256",
            name="uq_delivery_grants_candidate",
        ),
        sa.UniqueConstraint("workspace_id", "grant_key", name="uq_delivery_grants_key"),
    )
    op.create_table(
        "agent_send_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="reserved", nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'sending', 'sent', 'failed', 'unknown', 'blocked')",
            name="ck_agent_send_attempts_state",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "workspace_id"],
            ["delivery_grants.id", "delivery_grants.workspace_id"],
            name="fk_agent_send_attempts_grant_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grant_id", name="uq_agent_send_attempts_grant"),
    )


def downgrade() -> None:
    """Remove only the isolated Alpha capability tables."""

    op.drop_table("agent_send_attempts")
    op.drop_table("delivery_grants")
    op.drop_table("agent_installations")
