"""Join Watch archive and dialog snapshot schema histories.

Revision ID: 0121_watch_reply_join
Revises: 0120_watch_webhook_archive, 0120_reply_media_dialog_join
"""

revision = "0121_watch_reply_join"
down_revision = ("0120_watch_webhook_archive", "0120_reply_media_dialog_join")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
