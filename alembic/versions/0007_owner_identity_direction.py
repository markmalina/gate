"""Reclassify messages authored by any Telegram identity owned by Mark.

Revision ID: 0007_owner_identity_direction
Revises: 0006_business_message_direction
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_owner_identity_direction"
down_revision: str | None = "0006_business_message_direction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A person can own several Gate Telegram accounts. Treat a message as
    # owner-authored when its sender belongs to any active account with the
    # same owner, not only to the current Business connection.
    op.execute(
        """
        UPDATE messages AS message
        SET
            direction = 'outbound',
            recipient_id = COALESCE(message.recipient_id, message.chat_id),
            sent_at = COALESCE(message.sent_at, message.received_at, message.created_at),
            received_at = NULL
        FROM accounts AS message_account
        WHERE message.account_id = message_account.id
          AND message.source = 'telegram'
          AND message.business_connection_id IS NOT NULL
          AND message.direction = 'inbound'
          AND EXISTS (
              SELECT 1
              FROM accounts AS owner_account
              LEFT JOIN telegram_business_connections AS owner_connection
                ON owner_connection.account_id = owner_account.id
              WHERE owner_account.owner = message_account.owner
                AND owner_account.is_active
                AND message.sender_id IN (
                    owner_account.telegram_user_id::text,
                    owner_connection.telegram_user_id::text,
                    owner_connection.user_chat_id::text
                )
          )
        """
    )

    op.execute(
        """
        WITH activity AS (
            SELECT
                conversation.id AS conversation_id,
                MAX(message.created_at)
                    FILTER (WHERE message.direction = 'inbound') AS last_inbound_at,
                MAX(COALESCE(message.sent_at, message.created_at))
                    FILTER (WHERE message.direction = 'outbound') AS last_outbound_at,
                MAX(COALESCE(message.sent_at, message.received_at, message.created_at))
                    AS last_message_at
            FROM conversations AS conversation
            JOIN messages AS message
              ON message.account_id = conversation.account_id
             AND message.project_id = conversation.project_id
             AND message.chat_id = conversation.chat_id
             AND message.business_connection_id
                 IS NOT DISTINCT FROM conversation.business_connection_id
            GROUP BY conversation.id
        )
        UPDATE conversations AS conversation
        SET
            last_inbound_at = activity.last_inbound_at,
            last_outbound_at = activity.last_outbound_at,
            last_message_at = activity.last_message_at,
            requires_reply = (
                activity.last_inbound_at IS NOT NULL
                AND (
                    activity.last_outbound_at IS NULL
                    OR activity.last_inbound_at > activity.last_outbound_at
                )
            ),
            status = CASE
                WHEN activity.last_inbound_at IS NOT NULL
                 AND (
                    activity.last_outbound_at IS NULL
                    OR activity.last_inbound_at > activity.last_outbound_at
                 )
                THEN 'open'
                ELSE 'replied'
            END
        FROM activity
        WHERE conversation.id = activity.conversation_id
        """
    )

    op.execute(
        """
        UPDATE reply_tasks AS task
        SET
            status = 'replied',
            reason = CASE
                WHEN task.reason = 'inbound_human_text'
                THEN 'inbound_human_text_answered_by_owner'
                ELSE task.reason
            END,
            updated_at = NOW()
        FROM conversations AS conversation
        WHERE task.conversation_id = conversation.id
          AND task.status = 'needs_reply'
          AND conversation.last_outbound_at IS NOT NULL
          AND conversation.last_outbound_at >= task.created_at
        """
    )


def downgrade() -> None:
    # Correct owner identity facts and satisfied replies must not be reverted.
    pass
