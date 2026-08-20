"""Backfill Telegram photo captions into historical text projections.

Revision ID: 0099_telegram_caption_backfill
Revises: 0098_beta_space_search
Create Date: 2026-08-18

The source of truth is the durable raw Telegram payload already stored by Gate.
Only missing text fields are repaired; existing text is never overwritten.
"""

from alembic import op

revision = "0099_telegram_caption_backfill"
down_revision = "0098_beta_space_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH receipt_captions AS (
            SELECT
                receipt.id AS receipt_id,
                CASE
                    WHEN nullif(
                        btrim(receipt.payload #>> '{business_message,caption}'),
                        ''
                    ) IS NOT NULL
                    THEN receipt.payload #>> '{business_message,caption}'
                    WHEN nullif(
                        btrim(receipt.payload #>> '{message,caption}'),
                        ''
                    ) IS NOT NULL
                    THEN receipt.payload #>> '{message,caption}'
                    ELSE NULL
                END AS caption
            FROM telegram_receipts AS receipt
        )
        UPDATE canonical_messages AS canonical
        SET text_content = source.caption
        FROM receipt_captions AS source
        WHERE canonical.receipt_id = source.receipt_id
          AND canonical.text_content IS NULL
          AND source.caption IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE messages AS message
        SET text = canonical.text_content
        FROM canonical_messages AS canonical
        WHERE message.canonical_message_id = canonical.id
          AND message.text IS NULL
          AND canonical.text_content IS NOT NULL
        """
    )

    op.execute(
        """
        WITH update_captions AS (
            SELECT
                telegram_update.bot_id,
                telegram_update.update_id,
                CASE
                    WHEN nullif(
                        btrim(
                            telegram_update.payload
                            #>> '{business_message,caption}'
                        ),
                        ''
                    ) IS NOT NULL
                    THEN telegram_update.payload
                         #>> '{business_message,caption}'
                    WHEN nullif(
                        btrim(telegram_update.payload #>> '{message,caption}'),
                        ''
                    ) IS NOT NULL
                    THEN telegram_update.payload #>> '{message,caption}'
                    ELSE NULL
                END AS caption
            FROM telegram_updates AS telegram_update
        )
        UPDATE messages AS message
        SET text = source.caption
        FROM update_captions AS source
        WHERE message.bot_id = source.bot_id
          AND message.telegram_update_id = source.update_id
          AND message.text IS NULL
          AND source.caption IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE gate_events AS event
        SET text = message.text
        FROM messages AS message
        WHERE message.gate_event_id = event.id
          AND event.text IS NULL
          AND message.text IS NOT NULL
        """
    )


def downgrade() -> None:
    # Forward-only data repair: clearing restored text would destroy valid history.
    pass
