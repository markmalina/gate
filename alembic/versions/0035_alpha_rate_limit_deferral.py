"""Defer future Alpha rate limits and replay the bounded v2 test backlog.

Revision ID: 0035_alpha_rate_limit_deferral
Revises: 0034_alpha_model_v2_release
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_alpha_rate_limit_deferral"
down_revision: str | None = "0034_alpha_model_v2_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_DEPLOYMENT_KEY = "alpha-model-v2-openai"
_CONNECTOR_KEY = "telegram_alpha_group"
_BOT_USERNAME = "AlphaViaBot"


def upgrade() -> None:
    """Replay only v2 Alpha commands that reached canonical ingress without a turn."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            WITH eligible AS (
                SELECT DISTINCT receipt.id AS receipt_id
                FROM telegram_receipts AS receipt
                JOIN telegram_outbox_events AS event
                  ON event.receipt_id = receipt.id
                JOIN canonical_messages AS message
                  ON message.receipt_id = receipt.id
                JOIN workspaces AS workspace
                  ON workspace.id = message.workspace_id
                JOIN agent_deployments AS deployment
                  ON deployment.workspace_id = workspace.id
                 AND deployment.deployment_key = :deployment_key
                WHERE workspace.key = :workspace_key
                  AND workspace.is_active IS TRUE
                  AND receipt.bot_key = :connector_key
                  AND receipt.received_at >= deployment.created_at
                  AND receipt.status = 'processed'
                  AND event.status = 'delivered'
                  AND message.direction = 'inbound'
                  AND message.text_content ~* :command_pattern
                  AND NOT EXISTS (
                      SELECT 1
                      FROM alpha_usage_records AS usage
                      WHERE usage.trigger_message_id = message.id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM agent_turns AS turn
                      WHERE turn.trigger_message_id = message.id
                  )
            ),
            reset_receipts AS (
                UPDATE telegram_receipts AS receipt
                SET status = 'received',
                    attempt_count = 0,
                    processing_started_at = NULL,
                    processed_at = NULL,
                    next_attempt_at = now() + interval '2 minutes',
                    last_error_code = 'alpha_rate_limit_backfill',
                    last_error_at = now(),
                    updated_at = now()
                FROM eligible
                WHERE receipt.id = eligible.receipt_id
                RETURNING receipt.id
            )
            UPDATE telegram_outbox_events AS event
            SET status = 'retry_scheduled',
                attempt_count = 0,
                available_at = now() + interval '2 minutes',
                locked_at = NULL,
                delivered_at = NULL,
                last_error_code = 'alpha_rate_limit_backfill',
                last_error_at = now(),
                updated_at = now()
            FROM reset_receipts
            WHERE event.receipt_id = reset_receipts.id
            """
        ),
        {
            "workspace_key": _WORKSPACE_KEY,
            "deployment_key": _DEPLOYMENT_KEY,
            "connector_key": _CONNECTOR_KEY,
            "command_pattern": (
                rf"^[[:space:]]*/alpha(?:@{_BOT_USERNAME})?(?:[[:space:]]|$)"
            ),
        },
    )


def downgrade() -> None:
    """Keep replayed durable evidence; a completed retry is not safely reversible."""

    return None
