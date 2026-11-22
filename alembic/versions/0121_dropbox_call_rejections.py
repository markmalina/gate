"""Persist terminal Dropbox call intake metadata separately from audio."""

import sqlalchemy as sa
from alembic import op

revision = "0121_dropbox_call_rejections"
down_revision = "0120_watch_webhook_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dropbox_call_intake_rejections",
        sa.Column("workspace_key", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.String(255), primary_key=True),
        sa.Column("source_revision_sha256", sa.String(64), primary_key=True),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column(
            "rejected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("dropbox_call_intake_rejections")
