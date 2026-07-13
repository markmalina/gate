"""Backfill legacy Telegram Business routing from retained raw updates.

Revision ID: 0010_legacy_telegram_routing
Revises: 0009_account_routing_repair
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_legacy_telegram_routing"
down_revision: str | None = "0009_account_routing_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older normalized rows may have lost the Business connection even though
    # the immutable Telegram update still contains it.
    for table_name in ("gate_events", "messages"):
        op.execute(
            f"""
            UPDATE {table_name} AS item
            SET business_connection_id = raw.business_connection_id
            FROM (
                SELECT
                    telegram_update.bot_id,
                    telegram_update.update_id,
                    COALESCE(
                        telegram_update.payload
                            -> 'business_message' ->> 'business_connection_id',
                        telegram_update.payload
                            -> 'edited_business_message' ->> 'business_connection_id',
                        telegram_update.payload
                            -> 'deleted_business_messages' ->> 'business_connection_id',
                        telegram_update.payload
                            -> 'message' ->> 'business_connection_id'
                    ) AS business_connection_id
                FROM telegram_updates AS telegram_update
            ) AS raw
            WHERE item.bot_id = raw.bot_id
              AND item.telegram_update_id = raw.update_id
              AND item.business_connection_id IS NULL
              AND raw.business_connection_id IS NOT NULL
            """
        )

    # The persisted Business connection is the authoritative account route.
    for table_name in ("gate_events", "messages"):
        op.execute(
            f"""
            UPDATE {table_name} AS item
            SET account_id = connection.account_id
            FROM telegram_business_connections AS connection
            WHERE item.business_connection_id = connection.business_connection_id
              AND item.account_id IS DISTINCT FROM connection.account_id
            """
        )

    # Only repair a legacy conversation when all of its task-triggering
    # messages agree on one Business connection. Ambiguous histories remain
    # untouched and therefore fail closed.
    op.execute(
        """
        CREATE TEMP TABLE gate_legacy_conversation_route ON COMMIT DROP AS
        SELECT
            task.conversation_id,
            MIN(message.business_connection_id) AS business_connection_id,
            MIN(message.account_id::text)::uuid AS account_id
        FROM reply_tasks AS task
        JOIN messages AS message ON message.id = task.trigger_message_id
        WHERE message.business_connection_id IS NOT NULL
        GROUP BY task.conversation_id
        HAVING COUNT(DISTINCT message.business_connection_id) = 1
           AND COUNT(DISTINCT message.account_id) = 1
        """
    )

    op.execute("DROP INDEX IF EXISTS uq_conversations_context")
    op.execute(
        """
        UPDATE conversations AS conversation
        SET
            business_connection_id = route.business_connection_id,
            account_id = route.account_id
        FROM gate_legacy_conversation_route AS route
        WHERE conversation.id = route.conversation_id
          AND (
              conversation.business_connection_id
                  IS DISTINCT FROM route.business_connection_id
              OR conversation.account_id IS DISTINCT FROM route.account_id
          )
        """
    )

    # Routing repair can make a legacy default-account conversation collide
    # with a correctly routed one. Converge dependants before deleting copies.
    op.execute(
        """
        CREATE TEMP TABLE gate_legacy_conversation_merge ON COMMIT DROP AS
        WITH ranked AS (
            SELECT
                conversation.id AS source_id,
                FIRST_VALUE(conversation.id) OVER (
                    PARTITION BY
                        conversation.bot_id,
                        conversation.account_id,
                        conversation.project_id,
                        conversation.chat_id,
                        COALESCE(
                            conversation.business_connection_id,
                            '__gate_null_business_connection__'
                        )
                    ORDER BY
                        conversation.last_message_at DESC NULLS LAST,
                        conversation.updated_at DESC NULLS LAST,
                        conversation.created_at DESC,
                        conversation.id
                ) AS target_id,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        conversation.bot_id,
                        conversation.account_id,
                        conversation.project_id,
                        conversation.chat_id,
                        COALESCE(
                            conversation.business_connection_id,
                            '__gate_null_business_connection__'
                        )
                    ORDER BY
                        conversation.last_message_at DESC NULLS LAST,
                        conversation.updated_at DESC NULLS LAST,
                        conversation.created_at DESC,
                        conversation.id
                ) AS context_rank
            FROM conversations AS conversation
        )
        SELECT source_id, target_id
        FROM ranked
        WHERE context_rank > 1
        """
    )

    for table_name in (
        "reply_tasks",
        "reply_drafts",
        "conversation_context_bindings",
        "send_attempts",
    ):
        op.execute(
            f"""
            UPDATE {table_name} AS item
            SET conversation_id = merge.target_id
            FROM gate_legacy_conversation_merge AS merge
            WHERE item.conversation_id = merge.source_id
            """
        )

    op.execute(
        """
        DELETE FROM conversations AS conversation
        USING gate_legacy_conversation_merge AS merge
        WHERE conversation.id = merge.source_id
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversations_context
        ON conversations (
            bot_id,
            account_id,
            project_id,
            chat_id,
            COALESCE(
                business_connection_id,
                '__gate_null_business_connection__'
            )
        )
        """
    )

    # Direct trigger-message ownership is stronger than historical defaults.
    op.execute(
        """
        UPDATE reply_tasks AS task
        SET account_id = message.account_id
        FROM messages AS message
        WHERE task.trigger_message_id = message.id
          AND task.account_id IS DISTINCT FROM message.account_id
        """
    )
    op.execute(
        """
        UPDATE reply_drafts AS draft
        SET
            account_id = task.account_id,
            conversation_id = task.conversation_id
        FROM reply_tasks AS task
        WHERE draft.reply_task_id = task.id
          AND (
              draft.account_id IS DISTINCT FROM task.account_id
              OR draft.conversation_id IS DISTINCT FROM task.conversation_id
          )
        """
    )

    # Rebuild only routed conversation state, preserving empty histories.
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
            JOIN gate_legacy_conversation_route AS route
              ON route.conversation_id = conversation.id
            LEFT JOIN messages AS message
              ON message.account_id = conversation.account_id
             AND message.project_id = conversation.project_id
             AND message.chat_id = conversation.chat_id
             AND message.business_connection_id
                 IS NOT DISTINCT FROM conversation.business_connection_id
            GROUP BY conversation.id
        )
        UPDATE conversations AS conversation
        SET
            last_inbound_at = CASE
                WHEN activity.last_message_at IS NULL
                THEN conversation.last_inbound_at
                ELSE activity.last_inbound_at
            END,
            last_outbound_at = CASE
                WHEN activity.last_message_at IS NULL
                THEN conversation.last_outbound_at
                ELSE activity.last_outbound_at
            END,
            last_message_at = COALESCE(
                activity.last_message_at,
                conversation.last_message_at
            ),
            requires_reply = CASE
                WHEN activity.last_message_at IS NULL
                THEN conversation.requires_reply
                ELSE (
                    activity.last_inbound_at IS NOT NULL
                    AND (
                        activity.last_outbound_at IS NULL
                        OR activity.last_inbound_at > activity.last_outbound_at
                    )
                )
            END,
            status = CASE
                WHEN activity.last_message_at IS NULL
                THEN conversation.status
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


def downgrade() -> None:
    # This migration restores routing facts from immutable Telegram updates.
    # Reverting them to the legacy default account would be unsafe.
    pass
