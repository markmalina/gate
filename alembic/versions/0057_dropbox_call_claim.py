"""Retry one call after fixing concurrent Dropbox worker claims.

Revision ID: 0057_dropbox_call_claim
Revises: 0056_audio_provider_diagnostics
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_dropbox_call_claim"
down_revision: str | None = "0056_audio_provider_diagnostics"
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
            "AND transcription_reason_code = 'billing_reservation_replayed' "
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
            "transcription_reason_code = 'billing_reservation_replayed', "
            "transcribed_at = now() "
            "WHERE id = CAST(:recording_id AS uuid) "
            "AND transcription_status IN ('received', 'processing') "
            "AND transcript_text IS NULL "
            "AND size_bytes = 25505021"
        ),
        {"recording_id": _RECORDING_ID},
    )
