"""Add idempotent reply send attempts.

Revision ID: 0008_reply_send_attempts
Revises: 0007_owner_identity_direction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_reply_send_attempts"
down_revision: str | None = "0007_owner_identity_direction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "send_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "reply_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_drafts.id"),
            nullable=False,
        ),
        sa.Column(
            "reply_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_tasks.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("account_label", sa.String(length=32), nullable=False),
        sa.Column("target_chat_id", sa.String(length=255), nullable=False),
        sa.Column("business_connection_id", sa.String(length=255), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "reply_draft_id",
            name="uq_send_attempts_reply_draft_id",
        ),
    )
    op.create_index(
        "ix_send_attempts_reply_draft_id",
        "send_attempts",
        ["reply_draft_id"],
    )
    op.create_index(
        "ix_send_attempts_reply_task_id",
        "send_attempts",
        ["reply_task_id"],
    )
    op.create_index(
        "ix_send_attempts_conversation_id",
        "send_attempts",
        ["conversation_id"],
    )
    op.create_index(
        "ix_send_attempts_account_id",
        "send_attempts",
        ["account_id"],
    )
    op.create_index(
        "ix_send_attempts_project_id",
        "send_attempts",
        ["project_id"],
    )
    op.create_index(
        "ix_send_attempts_status_created_at",
        "send_attempts",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_send_attempts_status_created_at", table_name="send_attempts")
    op.drop_index("ix_send_attempts_project_id", table_name="send_attempts")
    op.drop_index("ix_send_attempts_account_id", table_name="send_attempts")
    op.drop_index("ix_send_attempts_conversation_id", table_name="send_attempts")
    op.drop_index("ix_send_attempts_reply_task_id", table_name="send_attempts")
    op.drop_index("ix_send_attempts_reply_draft_id", table_name="send_attempts")
    op.drop_table("send_attempts")
