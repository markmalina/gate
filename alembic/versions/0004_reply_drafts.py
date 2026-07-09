"""Add reply drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_reply_drafts"
down_revision: str | None = "0003_conversations_reply_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reply_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("reply_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="generated", nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column(
            "prompt_context",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["reply_task_id"], ["reply_tasks.id"]),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"]),
    )
    op.create_index(op.f("ix_reply_drafts_conversation_id"), "reply_drafts", ["conversation_id"])
    op.create_index(op.f("ix_reply_drafts_reply_task_id"), "reply_drafts", ["reply_task_id"])
    op.create_index("ix_reply_drafts_status_created_at", "reply_drafts", ["status", "created_at"])
    op.create_index(
        "ix_reply_drafts_task_created_at",
        "reply_drafts",
        ["reply_task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reply_drafts_task_created_at", table_name="reply_drafts")
    op.drop_index("ix_reply_drafts_status_created_at", table_name="reply_drafts")
    op.drop_index(op.f("ix_reply_drafts_reply_task_id"), table_name="reply_drafts")
    op.drop_index(op.f("ix_reply_drafts_conversation_id"), table_name="reply_drafts")
    op.drop_table("reply_drafts")
