"""Join released media packages and dialog revisions without rewriting either branch."""

revision = "0120_reply_media_dialog_join"
down_revision = ("0116_reply_media_packages", "0116_reply_dialog_versions")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
