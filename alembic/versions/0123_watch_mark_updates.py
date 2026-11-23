"""Persist requested and confirmed Watch card publication versions."""

import sqlalchemy as sa
from alembic import op

revision = "0123_watch_mark_updates"
down_revision = "0122_audio_processing_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "watch_voice_notes", sa.Column("mark_publish_version", sa.Integer(), nullable=False,
                                       server_default="0"),
    )
    op.add_column(
        "watch_early_cards", sa.Column("mark_published_version", sa.Integer(), nullable=False,
                                       server_default="0"),
    )
    op.add_column(
        "watch_early_cards", sa.Column("mark_update_retry_at", sa.DateTime(timezone=True)),
    )
    op.add_column("watch_early_cards", sa.Column("mark_update_error", sa.String(128)))
    op.add_column(
        "watch_early_cards", sa.Column("mark_update_blocked", sa.Boolean(), nullable=False,
                                       server_default=sa.false()),
    )
    op.add_column("watch_early_cards", sa.Column("mark_published_at", sa.DateTime(timezone=True)))
    op.add_column("watch_early_cards", sa.Column("mark_pending_part", sa.Integer()))
    op.add_column("watch_early_cards", sa.Column("mark_pending_digest", sa.String(64)))
    op.create_index(
        "ix_watch_note_mark_queue", "watch_voice_notes",
        ["workspace_key", "account_id", "id"],
        postgresql_where=sa.text("mark_publish_version > 0 AND webhook_archived"),
    )


def downgrade() -> None:
    op.drop_index("ix_watch_note_mark_queue", table_name="watch_voice_notes")
    for field in (
        "mark_published_at", "mark_pending_digest", "mark_pending_part",
        "mark_update_blocked", "mark_update_error", "mark_update_retry_at",
        "mark_published_version",
    ):
        op.drop_column("watch_early_cards", field)
    op.drop_column("watch_voice_notes", "mark_publish_version")
