"""Add isolated watch-voice originals, cursor and Mark delivery receipts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0112_watch_voice_inbox"
down_revision: str | None = "0111_reply_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watch_voice_cursors",
        sa.Column("workspace_key", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("cursor", sa.String(None), nullable=True),
        sa.PrimaryKeyConstraint("workspace_key", "account_id"),
    )
    op.create_table(
        "watch_voice_notes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_key", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("file_id", sa.String(255), nullable=False),
        sa.Column("revision", sa.String(255), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dropbox_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transcription_state", sa.String(16), nullable=False),
        sa.Column("transcript", sa.String(None), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_state", sa.String(16), nullable=False),
        sa.Column("next_part", sa.Integer(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("issue_url", sa.String(512), nullable=True),
        sa.Column("github_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "delivery_state IN ('pending','sending','unknown','delivered','blocked')",
            name="ck_watch_voice_delivery",
        ),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 26214400", name="ck_watch_voice_size"),
        sa.CheckConstraint(
            "transcription_state IN ('received','processing','transcribed','rejected','unknown')",
            name="ck_watch_voice_transcription",
        ),
        sa.UniqueConstraint(
            "workspace_key",
            "account_id",
            "file_id",
            "revision",
            name="uq_watch_voice_source_revision",
        ),
    )


def downgrade() -> None:
    op.drop_table("watch_voice_notes")
    op.drop_table("watch_voice_cursors")
