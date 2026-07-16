"""Add scoped canonical voice metadata and dormant Alpha voice jobs.

Revision ID: 0027_alpha_voice_intake
Revises: 0026_alpha_model_preflight
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_alpha_voice_intake"
down_revision: str | None = "0026_alpha_model_preflight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a queue-only foundation; no runtime or provider is enabled."""

    op.create_unique_constraint(
        "uq_agent_installations_id_dialog_workspace",
        "agent_installations",
        ["id", "dialog_id", "workspace_id"],
    )
    op.create_unique_constraint(
        "uq_canonical_messages_id_dialog_workspace",
        "canonical_messages",
        ["id", "dialog_id", "workspace_id"],
    )

    op.create_table(
        "canonical_voice_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "canonical_message_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("provider_file_id", sa.String(length=512), nullable=False),
        sa.Column("file_unique_id_sha256", sa.String(length=64), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(provider_file_id) BETWEEN 1 AND 512",
            name="ck_canonical_voice_media_file_id",
        ),
        sa.CheckConstraint(
            "file_unique_id_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_canonical_voice_media_unique_hash",
        ),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 1 AND 600",
            name="ck_canonical_voice_media_duration",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes BETWEEN 1 AND 19000000",
            name="ck_canonical_voice_media_size",
        ),
        sa.CheckConstraint(
            "mime_type IS NULL OR mime_type IN ('audio/ogg', 'audio/opus')",
            name="ck_canonical_voice_media_mime",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_canonical_voice_media_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_messages.id",
                "canonical_messages.dialog_id",
                "canonical_messages.workspace_id",
            ],
            name="fk_canonical_voice_media_message_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "canonical_message_id",
            name="uq_canonical_voice_media_message",
        ),
        sa.UniqueConstraint(
            "id",
            "canonical_message_id",
            "dialog_id",
            "workspace_id",
            name="uq_canonical_voice_media_id_message_scope",
        ),
    )

    op.create_table(
        "alpha_voice_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "canonical_message_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operation",
            sa.String(length=32),
            server_default=sa.text("'transcription'"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "intake_code",
            sa.String(length=128),
            server_default=sa.text("'alpha_voice_queued'"),
            nullable=False,
        ),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("provider_operation_ref", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "operation = 'transcription'",
            name="ck_alpha_voice_jobs_operation",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed', 'unknown')",
            name="ck_alpha_voice_jobs_state",
        ),
        sa.CheckConstraint(
            "attempts IN (0, 1)",
            name="ck_alpha_voice_jobs_attempts",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_alpha_voice_jobs_version",
        ),
        sa.CheckConstraint(
            "intake_code = 'alpha_voice_queued'",
            name="ck_alpha_voice_jobs_intake_code",
        ),
        sa.CheckConstraint(
            "transcript_text IS NULL OR char_length(transcript_text) <= 100000",
            name="ck_alpha_voice_jobs_transcript_size",
        ),
        sa.CheckConstraint(
            "(state = 'queued' AND attempts = 0 "
            "AND transcript_text IS NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NULL) OR "
            "(state = 'running' AND attempts = 1 "
            "AND transcript_text IS NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NOT NULL) OR "
            "(state = 'completed' AND attempts = 1 "
            "AND transcript_text IS NOT NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NOT NULL) OR "
            "(state IN ('failed', 'unknown') AND attempts = 1 "
            "AND transcript_text IS NULL AND error_code IS NOT NULL "
            "AND provider_operation_ref IS NOT NULL)",
            name="ck_alpha_voice_jobs_state_payload",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_alpha_voice_jobs_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_alpha_voice_jobs_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "dialog_id", "workspace_id"],
            [
                "agent_installations.id",
                "agent_installations.dialog_id",
                "agent_installations.workspace_id",
            ],
            name="fk_alpha_voice_jobs_installation_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_messages.id",
                "canonical_messages.dialog_id",
                "canonical_messages.workspace_id",
            ],
            name="fk_alpha_voice_jobs_message_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id", "canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_voice_media.id",
                "canonical_voice_media.canonical_message_id",
                "canonical_voice_media.dialog_id",
                "canonical_voice_media.workspace_id",
            ],
            name="fk_alpha_voice_jobs_media_message_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "canonical_message_id",
            "operation",
            name="uq_alpha_voice_jobs_message_operation",
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", name="uq_alpha_voice_jobs_id_workspace"
        ),
    )
    op.create_index(
        "ix_alpha_voice_jobs_workspace_state_created",
        "alpha_voice_jobs",
        ["workspace_id", "state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the dormant voice projection and queue."""

    op.drop_index(
        "ix_alpha_voice_jobs_workspace_state_created",
        table_name="alpha_voice_jobs",
    )
    op.drop_table("alpha_voice_jobs")
    op.drop_table("canonical_voice_media")
    op.drop_constraint(
        "uq_canonical_messages_id_dialog_workspace",
        "canonical_messages",
        type_="unique",
    )
    op.drop_constraint(
        "uq_agent_installations_id_dialog_workspace",
        "agent_installations",
        type_="unique",
    )
