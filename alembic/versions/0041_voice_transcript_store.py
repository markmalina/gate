"""Persist automatic Telegram voice transcripts beside original audio.

Revision ID: 0041_voice_transcript_store
Revises: 0040_alpha_model_v2_bounded_live
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_voice_transcript_store"
down_revision: str | None = "0040_alpha_model_v2_bounded_live"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_voice_archives",
        sa.Column("transcription_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column("transcript_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column("transcript_language", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column("transcription_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column(
            "transcription_reason_code",
            sa.String(length=128),
            nullable=True,
        ),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_telegram_voice_archives_transcription_status",
        "telegram_voice_archives",
        (
            "transcription_status IS NULL OR transcription_status IN "
            "('disabled', 'rejected', 'transcribed', 'unknown')"
        ),
    )
    op.create_check_constraint(
        "ck_telegram_voice_archives_transcript_shape",
        "telegram_voice_archives",
        (
            "(transcription_status IS NULL AND transcript_text IS NULL "
            "AND transcript_language IS NULL AND transcription_model IS NULL "
            "AND transcription_reason_code IS NULL AND transcribed_at IS NULL) "
            "OR (transcription_status = 'transcribed' "
            "AND length(transcript_text) > 0 "
            "AND transcript_language IS NOT NULL "
            "AND transcription_model IS NOT NULL "
            "AND transcription_reason_code IS NOT NULL "
            "AND transcribed_at IS NOT NULL) "
            "OR (transcription_status IN ('disabled', 'rejected', 'unknown') "
            "AND transcript_text IS NULL "
            "AND transcript_language IS NOT NULL "
            "AND transcription_model IS NOT NULL "
            "AND transcription_reason_code IS NOT NULL "
            "AND transcribed_at IS NOT NULL)"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_telegram_voice_archives_transcript_shape",
        "telegram_voice_archives",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_voice_archives_transcription_status",
        "telegram_voice_archives",
        type_="check",
    )
    op.drop_column("telegram_voice_archives", "transcribed_at")
    op.drop_column("telegram_voice_archives", "transcription_reason_code")
    op.drop_column("telegram_voice_archives", "transcription_model")
    op.drop_column("telegram_voice_archives", "transcript_language")
    op.drop_column("telegram_voice_archives", "transcript_text")
    op.drop_column("telegram_voice_archives", "transcription_status")
