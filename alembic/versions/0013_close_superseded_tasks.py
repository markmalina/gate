"""Close legacy reply tasks superseded by an outbound reply.

Revision ID: 0013_close_superseded_tasks
Revises: 0012_mark_workspace_owner
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_close_superseded_tasks"
down_revision: str | None = "0012_mark_workspace_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE reply_tasks AS task
        SET status = 'replied'
        FROM conversations AS conversation, messages AS trigger
        WHERE task.conversation_id = conversation.id
          AND task.trigger_message_id = trigger.id
          AND task.status = 'needs_reply'
          AND conversation.last_outbound_at IS NOT NULL
          AND COALESCE(trigger.received_at, trigger.created_at)
              <= conversation.last_outbound_at
        """
    )


def downgrade() -> None:
    # Superseded work must not be reopened after a confirmed later outbound
    # message.
    pass
