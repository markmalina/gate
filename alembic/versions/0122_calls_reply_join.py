"""Join call intake and dialog snapshot schema histories.

Revision ID: 0122_calls_reply_join
Revises: 0121_dropbox_call_rejections, 0121_watch_reply_join
"""

revision = "0122_calls_reply_join"
down_revision = ("0121_dropbox_call_rejections", "0121_watch_reply_join")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
