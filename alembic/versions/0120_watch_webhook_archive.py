"""Persist Watch archive intake alongside the early card and cursor."""

import sqlalchemy as sa
from alembic import op

revision = "0120_watch_webhook_archive"
down_revision = "0119_watch_webhook_wake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watch_early_cards", sa.Column("archive_input", sa.JSON()))
    op.add_column(
        "watch_early_cards", sa.Column("archive_retry_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "watch_voice_notes",
        sa.Column("webhook_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("watch_voice_notes", "webhook_archived")
    op.drop_column("watch_early_cards", "archive_retry_at")
    op.drop_column("watch_early_cards", "archive_input")
