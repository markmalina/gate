"""Add workspace-scoped canonical messaging foundation.

Revision ID: 0016_canonical_messaging_foundation
Revises: 0015_telegram_queue_incidents
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_canonical_messaging_foundation"
down_revision: str | None = "0015_telegram_queue_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("key", name="uq_workspaces_key"),
    )
    op.create_table(
        "transport_connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("connector_kind", sa.String(length=32), nullable=False),
        sa.Column("credential_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "connector_kind IN ('telegram_business', 'telegram_bot')",
            name="ck_transport_connectors_kind",
        ),
        sa.UniqueConstraint(
            "workspace_id", "connector_key", name="uq_transport_connectors_workspace_key"
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_transport_connectors_id_workspace"),
    )
    op.create_table(
        "dialogs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_dialog_id", sa.String(length=255), nullable=False),
        sa.Column("dialog_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dialog_kind IN ('private', 'group', 'supergroup', 'channel')",
            name="ck_dialogs_kind",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_dialogs_connector_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "connector_id",
            "provider_dialog_id",
            name="uq_dialogs_connector_provider",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_dialogs_id_workspace"),
    )
    op.create_index("ix_dialogs_workspace_kind", "dialogs", ["workspace_id", "dialog_kind"])
    op.create_table(
        "canonical_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("provider_sender_id", sa.String(length=255), nullable=True),
        sa.Column(
            "direction", sa.String(length=32), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'bot_or_service', 'unknown')",
            name="ck_canonical_messages_direction",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_canonical_messages_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("receipt_id", name="uq_canonical_messages_receipt_id"),
        sa.UniqueConstraint(
            "dialog_id", "provider_message_id", name="uq_canonical_messages_dialog_provider"
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_canonical_messages_id_workspace"),
    )
    op.create_index(
        "ix_canonical_messages_dialog_occurred",
        "canonical_messages",
        ["dialog_id", "occurred_at"],
    )
    op.create_table(
        "work_ownerships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("agent_key", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('human_managed', 'agent_managed', 'handoff_required', "
            "'paused', 'ignored', 'quarantined')",
            name="ck_work_ownerships_state",
        ),
        sa.CheckConstraint(
            "route IN ('human_reply_task', 'agent_turn', 'agent_handoff', "
            "'ignored', 'quarantined')",
            name="ck_work_ownerships_route",
        ),
        sa.CheckConstraint(
            "agent_key IS NULL OR agent_key IN ('alpha_group', 'oko')",
            name="ck_work_ownerships_agent_key",
        ),
        sa.CheckConstraint("version > 0", name="ck_work_ownerships_version"),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_work_ownerships_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_work_ownerships_message_workspace",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("dialog_id", name="uq_work_ownerships_dialog"),
    )
    op.create_index(
        "ix_work_ownerships_workspace_state",
        "work_ownerships",
        ["workspace_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_ownerships_workspace_state", table_name="work_ownerships")
    op.drop_table("work_ownerships")
    op.drop_index("ix_canonical_messages_dialog_occurred", table_name="canonical_messages")
    op.drop_table("canonical_messages")
    op.drop_index("ix_dialogs_workspace_kind", table_name="dialogs")
    op.drop_table("dialogs")
    op.drop_table("transport_connectors")
    op.drop_table("workspaces")
