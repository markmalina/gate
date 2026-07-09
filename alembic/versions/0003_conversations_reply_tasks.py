"""Add conversations and reply tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_conversations_reply_tasks"
down_revision: str | None = "0002_durable_message_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column("business_connection_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="open", nullable=False),
        sa.Column("requires_reply", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index(op.f("ix_conversations_bot_id"), "conversations", ["bot_id"])
    op.create_index(
        "ix_conversations_bot_chat_updated_at",
        "conversations",
        ["bot_id", "chat_id", "updated_at"],
    )
    op.create_index(
        "uq_conversations_context",
        "conversations",
        [
            "bot_id",
            sa.text("coalesce(account_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            sa.text("coalesce(project_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "chat_id",
            sa.text("coalesce(business_connection_id, '__gate_null_business_connection__')"),
        ],
        unique=True,
    )
    op.create_index(
        "ix_conversations_requires_reply", "conversations", ["requires_reply", "updated_at"]
    )

    op.create_table(
        "reply_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="needs_reply", nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"]),
        sa.UniqueConstraint("trigger_message_id", name="uq_reply_tasks_trigger_message_id"),
    )
    op.create_index(op.f("ix_reply_tasks_conversation_id"), "reply_tasks", ["conversation_id"])
    op.create_index("ix_reply_tasks_status_created_at", "reply_tasks", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_reply_tasks_status_created_at", table_name="reply_tasks")
    op.drop_index(op.f("ix_reply_tasks_conversation_id"), table_name="reply_tasks")
    op.drop_table("reply_tasks")
    op.drop_index("ix_conversations_requires_reply", table_name="conversations")
    op.drop_index("uq_conversations_context", table_name="conversations")
    op.drop_index("ix_conversations_bot_chat_updated_at", table_name="conversations")
    op.drop_index(op.f("ix_conversations_bot_id"), table_name="conversations")
    op.drop_table("conversations")
