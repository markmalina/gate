"""Reconcile the exact historical Telegram Business voice dead letters.

Revision ID: 0061_voice_reconcile
Revises: 0060_call_processing_status
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_voice_reconcile"
down_revision: str | None = "0060_call_processing_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CUTOFF = "2026-07-29T10:20:00+00:00"
_NOT_INBOUND = "telegram_voice_not_inbound"
_TRANSPORT_UNKNOWN = "openai_transcription_transport_unknown"
_EXPECTED_COUNTS = {_NOT_INBOUND: 9, _TRANSPORT_UNKNOWN: 3}
_ALLOWED_ARCHIVE_STATES = {
    _NOT_INBOUND: {"missing", "pending", "transcribed"},
    _TRANSPORT_UNKNOWN: {"unknown", "transcribed"},
}

_BASE_FILTER = """
r.bot_key = 'telegram_business'
AND r.received_at < CAST(:cutoff AS timestamptz)
AND r.status = 'dead_letter'
AND e.status = 'dead_letter'
AND e.receipt_id = r.id
AND e.last_error_code = r.last_error_code
AND r.last_error_code = :error_code
AND jsonb_typeof(r.payload -> 'business_message' -> 'voice') = 'object'
"""


def _classify(bind: sa.Connection) -> dict[tuple[str, str], int]:
    rows = bind.execute(
        sa.text(
            """
            SELECT
                e.last_error_code,
                CASE
                    WHEN a.id IS NULL THEN 'missing'
                    WHEN a.transcription_status IS NULL THEN 'pending'
                    ELSE a.transcription_status
                END AS archive_state,
                count(*)
            FROM telegram_receipts AS r
            JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
            LEFT JOIN telegram_voice_archives AS a ON a.receipt_id = r.id
            WHERE r.bot_key = 'telegram_business'
              AND r.received_at < CAST(:cutoff AS timestamptz)
              AND r.status = 'dead_letter'
              AND e.status = 'dead_letter'
              AND e.last_error_code = r.last_error_code
              AND e.last_error_code IN (
                  'telegram_voice_not_inbound',
                  'openai_transcription_transport_unknown'
              )
              AND jsonb_typeof(
                  r.payload -> 'business_message' -> 'voice'
              ) = 'object'
            GROUP BY e.last_error_code, archive_state
            """
        ),
        {"cutoff": _CUTOFF},
    ).all()
    return {
        (str(error_code), str(archive_state)): int(count)
        for error_code, archive_state, count in rows
    }


def _require_exact_scope(classified: dict[tuple[str, str], int]) -> None:
    totals = {
        error_code: sum(
            count
            for (candidate, _archive_state), count in classified.items()
            if candidate == error_code
        )
        for error_code in _EXPECTED_COUNTS
    }
    if totals != _EXPECTED_COUNTS:
        raise RuntimeError("voice_dead_letter_reconciliation_count_mismatch")
    for (error_code, archive_state), count in classified.items():
        if count < 1 or archive_state not in _ALLOWED_ARCHIVE_STATES[error_code]:
            raise RuntimeError("voice_dead_letter_reconciliation_state_mismatch")


def _requeue(
    bind: sa.Connection,
    *,
    error_code: str,
    archive_predicate: str,
) -> int:
    row = bind.execute(
        sa.text(
            f"""
            WITH eligible AS MATERIALIZED (
                SELECT r.id
                FROM telegram_receipts AS r
                JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
                WHERE {_BASE_FILTER}
                  AND ({archive_predicate})
            ),
            updated_events AS (
                UPDATE telegram_outbox_events AS e
                SET status = 'pending',
                    attempt_count = 0,
                    available_at = now(),
                    locked_at = NULL,
                    delivered_at = NULL,
                    last_error_code = NULL,
                    last_error_at = NULL
                FROM eligible
                WHERE e.receipt_id = eligible.id
                RETURNING e.receipt_id
            ),
            updated_receipts AS (
                UPDATE telegram_receipts AS r
                SET status = 'received',
                    attempt_count = 0,
                    processing_started_at = NULL,
                    processed_at = NULL,
                    next_attempt_at = NULL,
                    last_error_code = NULL,
                    last_error_at = NULL
                FROM eligible
                WHERE r.id = eligible.id
                RETURNING r.id
            )
            SELECT
                (SELECT count(*) FROM updated_events),
                (SELECT count(*) FROM updated_receipts)
            """
        ),
        {"cutoff": _CUTOFF, "error_code": error_code},
    ).one()
    event_count = int(row[0] or 0)
    receipt_count = int(row[1] or 0)
    if event_count != receipt_count:
        raise RuntimeError("voice_dead_letter_reconciliation_transition_mismatch")
    return event_count


def upgrade() -> None:
    bind = op.get_bind()
    classified = _classify(bind)
    _require_exact_scope(classified)

    not_inbound_requeued = _requeue(
        bind,
        error_code=_NOT_INBOUND,
        archive_predicate="TRUE",
    )
    transcribed_unknown_requeued = _requeue(
        bind,
        error_code=_TRANSPORT_UNKNOWN,
        archive_predicate=(
            "EXISTS ("
            "SELECT 1 FROM telegram_voice_archives AS a "
            "WHERE a.receipt_id = r.id "
            "AND a.transcription_status = 'transcribed'"
            ")"
        ),
    )
    authorized = bind.execute(
        sa.text(
            f"""
            UPDATE telegram_voice_archives AS a
            SET transcription_recovery_authorized_at = COALESCE(
                a.transcription_recovery_authorized_at,
                now()
            )
            FROM telegram_receipts AS r
            JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
            WHERE a.receipt_id = r.id
              AND {_BASE_FILTER}
              AND a.transcription_status = 'unknown'
            """
        ),
        {"cutoff": _CUTOFF, "error_code": _TRANSPORT_UNKNOWN},
    ).rowcount

    if not_inbound_requeued != _EXPECTED_COUNTS[_NOT_INBOUND]:
        raise RuntimeError("voice_dead_letter_reconciliation_requeue_mismatch")
    if transcribed_unknown_requeued + int(authorized or 0) != _EXPECTED_COUNTS[
        _TRANSPORT_UNKNOWN
    ]:
        raise RuntimeError("voice_dead_letter_reconciliation_recovery_mismatch")


def downgrade() -> None:
    """Withdraw unused recovery grants without recreating historical dead letters."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE telegram_voice_archives AS a
            SET transcription_recovery_authorized_at = NULL
            FROM telegram_receipts AS r
            JOIN telegram_outbox_events AS e ON e.receipt_id = r.id
            WHERE a.receipt_id = r.id
              AND {_BASE_FILTER}
              AND a.transcription_status = 'unknown'
            """
        ),
        {"cutoff": _CUTOFF, "error_code": _TRANSPORT_UNKNOWN},
    )
