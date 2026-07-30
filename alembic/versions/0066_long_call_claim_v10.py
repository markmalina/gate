"""Retry one exact rejected long call as three-minute PCM WAV parts.

Revision ID: 0067_long_call_claim_v10
Revises: 0066_long_call_claim_v9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_long_call_claim_v10"
down_revision: str | None = "0066_long_call_claim_v9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORDING_ID = "a1a82247-434e-4f43-8974-35a972ba9dc2"
_EXPECTED_REASON = "openai_transcription_rejected_http_400"
_EXPECTED_SIZE = 25_505_021
_RECOVERY_STATUS = "recovery_v10"
_CONSTRAINT = "ck_phone_call_recordings_transcription_status"
_TABLE = "phone_call_recordings"
_PRE_RECOVERY_STATUSES = (
    "received",
    "disabled",
    "rejected",
    "transcribed",
    "unknown",
    "processing",
    "recovery_v8",
    "recovery_v9",
)
_RECOVERY_STATUSES = (*_PRE_RECOVERY_STATUSES, _RECOVERY_STATUS)


def _status_check(statuses: tuple[str, ...]) -> str:
    values = ", ".join(f"'{status}'" for status in statuses)
    return f"transcription_status IN ({values})"


def _is_fresh_database(bind: sa.Connection) -> bool:
    count = bind.scalar(sa.text("SELECT count(*) FROM phone_call_recordings"))
    return int(count or 0) == 0


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        _status_check(_RECOVERY_STATUSES),
    )

    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            UPDATE phone_call_recordings
            SET transcription_status = :recovery_status,
                transcript_text = NULL,
                transcript_language = NULL,
                transcription_model = NULL,
                transcription_reason_code = NULL,
                transcribed_at = NULL,
                summary_status = NULL,
                summary_text = NULL,
                summary_model = NULL,
                summary_reason_code = NULL,
                summarized_at = NULL
            WHERE id = CAST(:recording_id AS uuid)
              AND transcription_status = 'rejected'
              AND transcription_reason_code = :expected_reason
              AND transcript_text IS NULL
              AND summary_text IS NULL
              AND size_bytes = :expected_size
              AND octet_length(data) = :expected_size
              AND duration_seconds > 0
              AND length(sha256) = 64
            """
        ),
        {
            "recording_id": _RECORDING_ID,
            "expected_reason": _EXPECTED_REASON,
            "expected_size": _EXPECTED_SIZE,
            "recovery_status": _RECOVERY_STATUS,
        },
    )
    if result.rowcount == 1 or (result.rowcount == 0 and _is_fresh_database(bind)):
        return
    raise RuntimeError("long_call_claim_v10_scope_mismatch")


def downgrade() -> None:
    """Withdraw an unclaimed recovery without overwriting provider work."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE phone_call_recordings
            SET transcription_status = 'rejected',
                transcript_language = 'ru',
                transcription_model = 'gpt-4o-mini-transcribe',
                transcription_reason_code = :expected_reason,
                transcribed_at = now()
            WHERE id = CAST(:recording_id AS uuid)
              AND transcription_status = :recovery_status
              AND transcript_text IS NULL
              AND summary_text IS NULL
              AND size_bytes = :expected_size
              AND octet_length(data) = :expected_size
            """
        ),
        {
            "recording_id": _RECORDING_ID,
            "expected_reason": _EXPECTED_REASON,
            "expected_size": _EXPECTED_SIZE,
            "recovery_status": _RECOVERY_STATUS,
        },
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        _status_check(_PRE_RECOVERY_STATUSES),
    )
