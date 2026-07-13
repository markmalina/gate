"""Repair Telegram Business account routing facts.

Revision ID: 0009_account_routing_repair
Revises: 0008_reply_send_attempts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_account_routing_repair"
down_revision: str | None = "0008_reply_send_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_RU_ID = "0d8e025f-323d-5e86-a44c-a620f690ae4f"
ACCOUNT_BY_ID = "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2"
OWNER_RU_TELEGRAM_ID = 7819816320
OWNER_BY_TELEGRAM_ID = 8648171437
CONNECTION_RU_ID = "hwOpkm0xiUoOHAAA866WWGZ4Bmk"
CONNECTION_BY_ID = "aE64a7bQiEocFgAAQqTwcX3xk2U"


def upgrade() -> None:
    # Persist the two owner identities so future connection events can be
    # verified independently of message direction.
    op.execute(
        f"""
        UPDATE accounts
        SET telegram_user_id = CASE
            WHEN id = '{ACCOUNT_RU_ID}'::uuid THEN {OWNER_RU_TELEGRAM_ID}
            WHEN id = '{ACCOUNT_BY_ID}'::uuid THEN {OWNER_BY_TELEGRAM_ID}
            ELSE telegram_user_id
        END
        WHERE id IN ('{ACCOUNT_RU_ID}'::uuid, '{ACCOUNT_BY_ID}'::uuid)
        """
    )

    # Before moving connection rows, keep only the newest enabled connection
    # for each resulting account. This preserves the partial unique index.
    op.execute(
        f"""
        WITH desired AS (
            SELECT
                connection.id,
                connection.is_enabled,
                connection.last_update_at,
                connection.created_at,
                CASE
                    WHEN connection.business_connection_id = '{CONNECTION_BY_ID}'
                      OR connection.telegram_user_id = {OWNER_BY_TELEGRAM_ID}
                      OR connection.user_chat_id = {OWNER_BY_TELEGRAM_ID}
                    THEN '{ACCOUNT_BY_ID}'::uuid
                    WHEN connection.business_connection_id = '{CONNECTION_RU_ID}'
                      OR connection.telegram_user_id = {OWNER_RU_TELEGRAM_ID}
                      OR connection.user_chat_id = {OWNER_RU_TELEGRAM_ID}
                    THEN '{ACCOUNT_RU_ID}'::uuid
                    ELSE connection.account_id
                END AS desired_account_id
            FROM telegram_business_connections AS connection
            WHERE connection.account_id IN (
                    '{ACCOUNT_RU_ID}'::uuid,
                    '{ACCOUNT_BY_ID}'::uuid
                )
               OR connection.business_connection_id IN (
                    '{CONNECTION_RU_ID}',
                    '{CONNECTION_BY_ID}'
                )
               OR connection.telegram_user_id IN (
                    {OWNER_RU_TELEGRAM_ID},
                    {OWNER_BY_TELEGRAM_ID}
                )
               OR connection.user_chat_id IN (
                    {OWNER_RU_TELEGRAM_ID},
                    {OWNER_BY_TELEGRAM_ID}
                )
        ),
        ranked AS (
            SELECT
                desired.id,
                desired.is_enabled,
                ROW_NUMBER() OVER (
                    PARTITION BY desired.desired_account_id
                    ORDER BY
                        desired.is_enabled DESC,
                        desired.last_update_at DESC,
                        desired.created_at DESC,
                        desired.id
                ) AS enabled_rank
            FROM desired
        )
        UPDATE telegram_business_connections AS connection
        SET is_enabled = false
        FROM ranked
        WHERE connection.id = ranked.id
          AND connection.is_enabled
          AND ranked.enabled_rank > 1
        """
    )

    op.execute(
        f"""
        UPDATE telegram_business_connections AS connection
        SET account_id = CASE
            WHEN connection.business_connection_id = '{CONNECTION_BY_ID}'
              OR connection.telegram_user_id = {OWNER_BY_TELEGRAM_ID}
              OR connection.user_chat_id = {OWNER_BY_TELEGRAM_ID}
            THEN '{ACCOUNT_BY_ID}'::uuid
            WHEN connection.business_connection_id = '{CONNECTION_RU_ID}'
              OR connection.telegram_user_id = {OWNER_RU_TELEGRAM_ID}
              OR connection.user_chat_id = {OWNER_RU_TELEGRAM_ID}
            THEN '{ACCOUNT_RU_ID}'::uuid
            ELSE connection.account_id
        END
        WHERE connection.business_connection_id IN (
                '{CONNECTION_RU_ID}',
                '{CONNECTION_BY_ID}'
            )
           OR connection.telegram_user_id IN (
                {OWNER_RU_TELEGRAM_ID},
                {OWNER_BY_TELEGRAM_ID}
            )
           OR connection.user_chat_id IN (
                {OWNER_RU_TELEGRAM_ID},
                {OWNER_BY_TELEGRAM_ID}
            )
        """
    )

    # Business connection is the source of truth for stored Telegram facts.
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

    # A correctly routed BY conversation can already coexist with an older
    # copy that was defaulted to RU. Drop the context index while converging
    # ownership, then merge duplicate contexts before recreating it.
    op.execute("DROP INDEX IF EXISTS uq_conversations_context")
    op.execute(
        """
        UPDATE conversations AS conversation
        SET account_id = connection.account_id
        FROM telegram_business_connections AS connection
        WHERE conversation.business_connection_id = connection.business_connection_id
          AND conversation.account_id IS DISTINCT FROM connection.account_id
        """
    )
    op.execute(
        """
        CREATE TEMP TABLE gate_conversation_merge ON COMMIT DROP AS
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
            FROM gate_conversation_merge AS merge
            WHERE item.conversation_id = merge.source_id
            """
        )

    op.execute(
        """
        DELETE FROM conversations AS conversation
        USING gate_conversation_merge AS merge
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

    # Rebuild the merged conversation state from message facts.
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

    # Derived reply objects inherit the repaired conversation account.
    for table_name in ("reply_tasks", "reply_drafts"):
        op.execute(
            f"""
            UPDATE {table_name} AS item
            SET account_id = conversation.account_id
            FROM conversations AS conversation
            WHERE item.conversation_id = conversation.id
              AND conversation.account_id IS NOT NULL
              AND item.account_id IS DISTINCT FROM conversation.account_id
            """
        )

    op.execute(
        """
        UPDATE send_attempts AS attempt
        SET
            account_id = connection.account_id,
            account_label = account.country_code
        FROM telegram_business_connections AS connection
        JOIN accounts AS account ON account.id = connection.account_id
        WHERE attempt.business_connection_id = connection.business_connection_id
          AND (
              attempt.account_id IS DISTINCT FROM connection.account_id
              OR attempt.account_label IS DISTINCT FROM account.country_code
          )
        """
    )

    op.execute(
        """
        UPDATE audit_logs AS audit
        SET account_id = event.account_id
        FROM gate_events AS event
        WHERE audit.gate_event_id = event.id
          AND audit.account_id IS DISTINCT FROM event.account_id
        """
    )


def downgrade() -> None:
    # This migration repairs ownership facts. Reverting them to a known-wrong
    # account would make reads and approved sends unsafe.
    pass
