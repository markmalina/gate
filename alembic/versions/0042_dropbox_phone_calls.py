"""Add durable Dropbox mobile-call intake and transcript storage.

Revision ID: 0042_dropbox_phone_calls
Revises: 0041_voice_transcript_store
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_dropbox_phone_calls"
down_revision: str | None = "0041_voice_transcript_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dropbox_call_inbox_states",
        sa.Column("account_id", sa.String(length=255), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("pending", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("webhook_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "phone_call_recordings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_key", sa.String(length=128), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_kind",
            sa.String(length=32),
            server_default=sa.text("'phone_call_dropbox'"),
            nullable=False,
        ),
        sa.Column("source_account_id", sa.String(length=255), nullable=False),
        sa.Column("source_file_id", sa.String(length=255), nullable=False),
        sa.Column("source_revision", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "transcription_status",
            sa.String(length=16),
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("transcript_language", sa.String(length=32), nullable=True),
        sa.Column("transcription_model", sa.String(length=64), nullable=True),
        sa.Column("transcription_reason_code", sa.String(length=128), nullable=True),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind = 'phone_call_dropbox'",
            name="ck_phone_call_recordings_source_kind",
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 20971520",
            name="ck_phone_call_recordings_bounded_size",
        ),
        sa.CheckConstraint(
            "transcription_status IN "
            "('received', 'disabled', 'rejected', 'transcribed', 'unknown')",
            name="ck_phone_call_recordings_transcription_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_account_id",
            "source_file_id",
            "source_revision",
            name="uq_phone_call_recordings_source_revision",
        ),
    )
    op.create_index(
        "ix_phone_call_recordings_workspace_received",
        "phone_call_recordings",
        ["workspace_key", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_phone_call_recordings_workspace_received",
        table_name="phone_call_recordings",
    )
    op.drop_table("phone_call_recordings")
    op.drop_table("dropbox_call_inbox_states")
