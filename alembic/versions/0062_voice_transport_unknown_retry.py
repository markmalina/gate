"""Authorize one exact isolated retry for the remaining transport-unknown voice.

Revision ID: 0062_voice_unknown_retry
Revises: 0061_voice_reconcile
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_voice_unknown_retry"
down_revision: str | None = "0061_voice_reconcile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRANSPORT_UNKNOWN = "openai_transcription_transport_unknown"
_RECOVERY_GENERATION = 2
_SCOPE_FILTER = """
r.bot_key = 'telegram_business'
AND r.status = 'dead_letter'
AND r.last_error_code = :error_code
AND e.receipt_id = r.id
AND e.status = 'dead_letter'
AND e.last_error_code = :error_code
AND jsonb_typeof(r.payload -> 'business_message' -> 'voice') = 'object'
AND a.receipt_id = r.id
AND a.transcription_status = 'unknown'
AND a.transcript_text IS NULL
AND a.summary_text IS NULL
AND COALESCE(a.summary_status, '') <> 'summarized'
AND a.transcription_recovery_authorized_at IS NULL
AND a.size_bytes > 0
AND a.duration_seconds > 0
AND length(a.sha256) = 64
AND octet_length(a.data) > 0
"""


def _is_fresh_database(bind: sa.Connection) -> bool:
    receipt_count = bind.scalar(sa.text("SELECT count(*) FROM telegram_receipts"))
    return int(receipt_count or 0) == 0


def _require_exact_scope(candidate_count: int) -> None:
    if candidate_count != 1:
        raise RuntimeError("voice_transport_unknown_retry_scope_mismatch")


def _authorize_exact_retry(bind: sa.Connection) -> tuple[int, int]:
    row = bind.execute(
        sa.text(
            f"""
            WITH eligible AS MATERIALIZED (
                SELECT a.id
                FROM telegram_voice_archives AS a
                JOIN telegram_receipts AS r ON r.id = a.receipt_id
                JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
                WHERE {_SCOPE_FILTER}
                  AND a.transcription_recovery_generation IS NULL
                FOR UPDATE OF a
            ),
            scope AS (
                SELECT count(*) AS candidate_count
                FROM eligible
            ),
            updated AS (
                UPDATE telegram_voice_archives AS a
                SET transcription_recovery_generation = :recovery_generation
                FROM eligible, scope
                WHERE a.id = eligible.id
                  AND scope.candidate_count = 1
                RETURNING a.id
            )
            SELECT
                scope.candidate_count,
                (SELECT count(*) FROM updated)
            FROM scope
            """
        ),
        {
            "error_code": _TRANSPORT_UNKNOWN,
            "recovery_generation": _RECOVERY_GENERATION,
        },
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def upgrade() -> None:
    op.add_column(
        "telegram_voice_archives",
        sa.Column(
            "transcription_recovery_generation",
            sa.Integer(),
            nullable=True,
        ),
    )
    bind = op.get_bind()
    candidate_count, authorized_count = _authorize_exact_retry(bind)
    if candidate_count == 0 and _is_fresh_database(bind):
        return
    _require_exact_scope(candidate_count)
    if authorized_count != 1:
        raise RuntimeError("voice_transport_unknown_retry_authorization_mismatch")


def downgrade() -> None:
    """Withdraw the isolated grant without changing queue or provider state."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE telegram_voice_archives AS a
            SET transcription_recovery_generation = NULL
            FROM telegram_receipts AS r
            JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
            WHERE {_SCOPE_FILTER}
              AND a.transcription_recovery_generation = :recovery_generation
            """
        ),
        {
            "error_code": _TRANSPORT_UNKNOWN,
            "recovery_generation": _RECOVERY_GENERATION,
        },
    )
    op.drop_column(
        "telegram_voice_archives",
        "transcription_recovery_generation",
    )
