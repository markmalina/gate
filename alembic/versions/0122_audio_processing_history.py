"""Add nullable processing milestones without guessing historical timestamps."""

import sqlalchemy as sa
from alembic import op

revision = "0122_audio_processing_history"
down_revision = "0121_dropbox_call_rejections"
branch_labels = None
depends_on = None

_TABLES = ("watch_voice_notes", "phone_call_recordings", "telegram_voice_archives")
_TIMES = (
    "transcription_queued_at",
    "recognition_started_at",
    "text_saved_at",
    "transcription_stopped_at",
)


def upgrade() -> None:
    for table in _TABLES:
        for field in _TIMES:
            op.add_column(table, sa.Column(field, sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("transcription_stop_reason", sa.String(128), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "transcription_stop_reason")
        for field in reversed(_TIMES):
            op.drop_column(table, field)
