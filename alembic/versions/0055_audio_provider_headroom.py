"""Retry one oversized call with additional provider request headroom.

Revision ID: 0055_audio_provider_headroom
Revises: 0054_audio_provider_limit
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_audio_provider_headroom"
down_revision: str | None = "0054_audio_provider_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORDING_ID = "a1a82247-434e-4f43-8974-35a972ba9dc2"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE phone_call_recordings "
            "SET transcription_status = 'received', "
            "transcript_text = NULL, "
            "transcript_language = NULL, "
            "transcription_model = NULL, "
            "transcription_reason_code = NULL, "
            "transcribed_at = NULL "
            "WHERE id = CAST(:recording_id AS uuid) "
            "AND transcription_status = 'rejected' "
            "AND transcription_reason_code = 'openai_transcription_rejected' "
            "AND size_bytes = 25505021"
        ),
        {"recording_id": _RECORDING_ID},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE phone_call_recordings "
            "SET transcription_status = 'rejected', "
            "transcript_language = 'ru', "
            "transcription_model = 'gpt-4o-mini-transcribe', "
            "transcription_reason_code = 'openai_transcription_rejected', "
            "transcribed_at = now() "
            "WHERE id = CAST(:recording_id AS uuid) "
            "AND transcription_status = 'received' "
            "AND transcript_text IS NULL "
            "AND size_bytes = 25505021"
        ),
        {"recording_id": _RECORDING_ID},
    )
