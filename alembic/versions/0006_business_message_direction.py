"""Reclassify Telegram Business messages sent by the account owner.

Revision ID: 0006_business_message_direction
Revises: 0005_multi_account_routing
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_business_message_direction"
down_revision: str | None = "0005_multi_account_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sprint 14 originally classified every observed Business message as inbound.
    # Reclassify messages whose sender is the owner of the Business connection.
    op.execute(
        """
        UPDATE messages AS message
        SET
            direction = 'outbound',
            recipient_id = COALESCE(message.recipient_id, message.chat_id),
            sent_at = COALESCE(message.sent_at, message.received_at, message.created_at),
            received_at = NULL
        FROM telegram_business_connections AS connection
        WHERE message.business_connection_id = connection.business_connection_id
          AND message.direction = 'inbound'
          AND message.sender_id IN (
              connection.telegram_user_id::text,
              connection.user_chat_id::text
          )
        """
    )

    # Rebuild conversation direction timestamps from the corrected message facts.
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

    # Any task followed by an observed outbound answer is already satisfied.
    op.execute(
        """
        UPDATE reply_tasks AS task
        SET
            status = 'replied',
            reason = CASE
                WHEN task.reason = 'inbound_human_text'
                THEN 'inbound_human_text_answered_by_observed_outbound'
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
    # This is a corrective data migration. Reintroducing known-wrong directions
    # and reopening already answered tasks would be unsafe, so downgrade is a no-op.
    pass
