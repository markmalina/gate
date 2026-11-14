"""Metadata-only early Watch cards, independent of legacy transcription state."""

import sqlalchemy as sa
from alembic import op

revision = "0117_watch_early_cards"
down_revision = "0116_reply_media_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_schedule_control",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("last_slot", sa.Integer(), nullable=False),
        sa.Column("last_outcome", sa.String(32), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('idle','inflight','unknown')", name="ck_watch_schedule_state"
        ),
    )
    op.create_table(
        "watch_early_cursors",
        sa.Column("workspace_key", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.String(255), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("cutover_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "watch_early_rejections",
        sa.Column("workspace_key", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.String(255), primary_key=True),
        sa.Column("metadata_sha256", sa.String(64), primary_key=True),
        sa.Column("file_id", sa.String(255), nullable=True),
        sa.Column("revision", sa.String(255), nullable=True),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "watch_early_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_key", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("file_id", sa.String(255), nullable=False),
        sa.Column("revision", sa.String(255), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("issue_url", sa.String(512), nullable=True),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workspace_key", "account_id", "file_id", "revision", name="uq_watch_early_source"
        ),
        sa.CheckConstraint(
            "state IN ('pending','sending','unknown','saved','blocked')",
            name="ck_watch_early_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("watch_early_cards")
    op.drop_table("watch_early_rejections")
    op.drop_table("watch_early_cursors")
    op.drop_table("watch_schedule_control")
