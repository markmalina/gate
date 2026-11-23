"""Join the audio processing history and reply protection branches."""

revision = "0123_audio_reply_join"
down_revision = ("0122_audio_processing_history", "0122_calls_reply_join")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
