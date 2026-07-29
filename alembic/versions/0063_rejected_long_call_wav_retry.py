"""Retry one exact rejected long call as bounded PCM WAV parts.

Revision ID: 0064_long_call_wav_retry
Revises: 0063_voice_unknown_retry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_long_call_wav_retry"
down_revision: str | None = "0063_voice_unknown_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORDING_ID = "a1a82247-434e-4f43-8974-35a972ba9dc2"
_EXPECTED_REASON = "openai_transcription_rejected_http_400"
_EXPECTED_SIZE = 25_505_021


def _is_fresh_database(bind: sa.Connection) -> bool:
    count = bind.scalar(sa.text("SELECT count(*) FROM phone_call_recordings"))
    return int(count or 0) == 0


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            UPDATE phone_call_recordings
            SET transcription_status = 'received',
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
        },
    )
    if result.rowcount == 1 or (result.rowcount == 0 and _is_fresh_database(bind)):
        return
    raise RuntimeError("long_call_wav_retry_scope_mismatch")


def downgrade() -> None:
    """Stop an unclaimed retry without overwriting provider work."""

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
              AND transcription_status = 'received'
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
        },
    )
