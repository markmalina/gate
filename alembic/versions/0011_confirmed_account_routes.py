"""Apply operator-confirmed Telegram account corrections.

Revision ID: 0011_confirmed_account_routes
Revises: 0010_legacy_telegram_routing
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_confirmed_account_routes"
down_revision: str | None = "0010_legacy_telegram_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_BY_ID = "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2"
CONNECTION_BY_ID = "aE64a7bQiEocFgAAQqTwcX3xk2U"
CONFIRMED_BY_CHAT_IDS = ("8417736726", "770412450")


def upgrade() -> None:
    chat_ids = ", ".join(f"'{chat_id}'" for chat_id in CONFIRMED_BY_CHAT_IDS)

    # Mark confirmed these counterparties as belonging to his BY Telegram
    # Business account. The correction is explicit and intentionally narrow.
    for table_name in ("gate_events", "messages"):
        op.execute(
            f"""
            UPDATE {table_name}
            SET
                account_id = '{ACCOUNT_BY_ID}'::uuid,
                business_connection_id = '{CONNECTION_BY_ID}'
            WHERE chat_id IN ({chat_ids})
              AND (
                  account_id IS DISTINCT FROM '{ACCOUNT_BY_ID}'::uuid
                  OR business_connection_id
                      IS DISTINCT FROM '{CONNECTION_BY_ID}'
              )
            """
        )

    op.execute("DROP INDEX IF EXISTS uq_conversations_context")
    op.execute(
        f"""
        UPDATE conversations
        SET
            account_id = '{ACCOUNT_BY_ID}'::uuid,
            business_connection_id = '{CONNECTION_BY_ID}'
        WHERE chat_id IN ({chat_ids})
          AND (
              account_id IS DISTINCT FROM '{ACCOUNT_BY_ID}'::uuid
              OR business_connection_id IS DISTINCT FROM '{CONNECTION_BY_ID}'
          )
        """
    )

    op.execute(
        """
        CREATE TEMP TABLE gate_confirmed_conversation_merge ON COMMIT DROP AS
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
            FROM gate_confirmed_conversation_merge AS merge
            WHERE item.conversation_id = merge.source_id
            """
        )

    op.execute(
        """
        DELETE FROM conversations AS conversation
        USING gate_confirmed_conversation_merge AS merge
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

    op.execute(
        f"""
        UPDATE reply_tasks AS task
        SET account_id = '{ACCOUNT_BY_ID}'::uuid
        FROM messages AS message
        WHERE task.trigger_message_id = message.id
          AND message.chat_id IN ({chat_ids})
          AND task.account_id IS DISTINCT FROM '{ACCOUNT_BY_ID}'::uuid
        """
    )
    op.execute(
        f"""
        UPDATE reply_drafts AS draft
        SET
            account_id = '{ACCOUNT_BY_ID}'::uuid,
            conversation_id = task.conversation_id
        FROM reply_tasks AS task
        JOIN messages AS message ON message.id = task.trigger_message_id
        WHERE draft.reply_task_id = task.id
          AND message.chat_id IN ({chat_ids})
          AND (
              draft.account_id IS DISTINCT FROM '{ACCOUNT_BY_ID}'::uuid
              OR draft.conversation_id IS DISTINCT FROM task.conversation_id
          )
        """
    )

    op.execute(
        f"""
        UPDATE send_attempts AS attempt
        SET
            account_id = '{ACCOUNT_BY_ID}'::uuid,
            account_label = 'BY',
            business_connection_id = '{CONNECTION_BY_ID}'
        FROM conversations AS conversation
        WHERE attempt.conversation_id = conversation.id
          AND conversation.chat_id IN ({chat_ids})
          AND (
              attempt.account_id IS DISTINCT FROM '{ACCOUNT_BY_ID}'::uuid
              OR attempt.account_label IS DISTINCT FROM 'BY'
              OR attempt.business_connection_id
                  IS DISTINCT FROM '{CONNECTION_BY_ID}'
          )
        """
    )


def downgrade() -> None:
    # Operator-confirmed ownership must not be reverted to the known-wrong
    # legacy default account.
    pass
