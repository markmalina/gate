"""Create permanent private Alfa transcription jobs.

Revision ID: 0083_alpha_transcription_product
Revises: 0082_become_quest
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0083_alpha_transcription_product"
down_revision: str | None = "0082_become_quest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alpha_transcription_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_media_type", sa.String(length=128), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("analysis_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("language", sa.String(length=32), server_default="ru", nullable=False),
        sa.Column(
            "model",
            sa.String(length=128),
            server_default="gpt-4o-mini-transcribe",
            nullable=False,
        ),
        sa.Column("duration_millis", sa.BigInteger(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("billed_quantity", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("actual_microusd", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("provider_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verbatim_path", sa.String(length=1024), nullable=True),
        sa.Column("verbatim_sha256", sa.String(length=64), nullable=True),
        sa.Column("clean_path", sa.String(length=1024), nullable=True),
        sa.Column("clean_sha256", sa.String(length=64), nullable=True),
        sa.Column("analysis_path", sa.String(length=1024), nullable=True),
        sa.Column("analysis_sha256", sa.String(length=64), nullable=True),
        sa.Column("manifest_path", sa.String(length=1024), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "state IN ('queued','preparing','transcribing','assembling','completed',"
            "'failed','unknown','duplicate')",
            name="ck_alpha_transcription_job_state",
        ),
        sa.CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alpha_transcription_job_source_sha",
        ),
        sa.CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes BETWEEN 1 AND 2147483648",
            name="ck_alpha_transcription_job_source_size",
        ),
        sa.CheckConstraint(
            "duration_millis IS NULL OR duration_millis BETWEEN 1 AND 86400000",
            name="ck_alpha_transcription_job_duration",
        ),
        sa.CheckConstraint(
            "chunk_count BETWEEN 0 AND 512",
            name="ck_alpha_transcription_job_chunk_count",
        ),
        sa.CheckConstraint(
            "completed_chunk_count BETWEEN 0 AND chunk_count",
            name="ck_alpha_transcription_job_completed_chunks",
        ),
        sa.CheckConstraint(
            "billed_quantity >= 0 AND actual_microusd >= 0",
            name="ck_alpha_transcription_job_usage",
        ),
        sa.CheckConstraint(
            "canonical_job_id IS NULL OR state = 'duplicate'",
            name="ck_alpha_transcription_job_duplicate_binding",
        ),
        sa.CheckConstraint(
            "state <> 'completed' OR (source_sha256 IS NOT NULL AND chunk_count > 0 "
            "AND completed_chunk_count = chunk_count AND verbatim_path IS NOT NULL "
            "AND clean_path IS NOT NULL AND manifest_path IS NOT NULL "
            "AND error_code IS NULL)",
            name="ck_alpha_transcription_job_completed_payload",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["canonical_job_id"],
            ["alpha_transcription_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_sha256",
            name="uq_alpha_transcription_job_workspace_source",
        ),
    )
    op.create_index(
        "ix_alpha_transcription_job_workspace_state_created",
        "alpha_transcription_jobs",
        ["workspace_id", "state", "created_at"],
    )
    op.create_index(
        "ix_alpha_transcription_job_state_next_attempt",
        "alpha_transcription_jobs",
        ["state", "next_attempt_at"],
    )

    op.create_table(
        "alpha_transcription_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("attempt_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("start_millis", sa.BigInteger(), nullable=False),
        sa.Column("end_millis", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_operation_ref", sa.String(length=255), nullable=True),
        sa.Column("billed_quantity", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("actual_microusd", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("transcript_path", sa.String(length=1024), nullable=True),
        sa.Column("transcript_sha256", sa.String(length=64), nullable=True),
        sa.Column("transcript_chars", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("part_index BETWEEN 0 AND 511", name="ck_alpha_tx_part_index"),
        sa.CheckConstraint("attempt_version > 0", name="ck_alpha_tx_part_attempt_version"),
        sa.CheckConstraint(
            "start_millis >= 0 AND end_millis > start_millis",
            name="ck_alpha_tx_part_interval",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','completed','failed','unknown')",
            name="ck_alpha_tx_part_state",
        ),
        sa.CheckConstraint("attempts IN (0,1)", name="ck_alpha_tx_part_attempts"),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alpha_tx_part_source_sha",
        ),
        sa.CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 25000000",
            name="ck_alpha_tx_part_source_size",
        ),
        sa.CheckConstraint(
            "billed_quantity >= 0 AND actual_microusd >= 0",
            name="ck_alpha_tx_part_usage",
        ),
        sa.CheckConstraint(
            "(state = 'queued' AND attempts = 0 AND provider_operation_ref IS NULL "
            "AND transcript_path IS NULL AND error_code IS NULL) OR "
            "(state = 'running' AND attempts = 1 AND transcript_path IS NULL "
            "AND error_code IS NULL) OR "
            "(state = 'completed' AND attempts = 1 AND transcript_path IS NOT NULL "
            "AND transcript_sha256 IS NOT NULL AND error_code IS NULL) OR "
            "(state IN ('failed','unknown') AND attempts = 1 AND transcript_path IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_alpha_tx_part_state_payload",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["alpha_transcription_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "part_index",
            "attempt_version",
            name="uq_alpha_transcription_part_attempt",
        ),
    )
    op.create_index(
        "ix_alpha_transcription_part_job_state",
        "alpha_transcription_parts",
        ["job_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alpha_transcription_part_job_state",
        table_name="alpha_transcription_parts",
    )
    op.drop_table("alpha_transcription_parts")
    op.drop_index(
        "ix_alpha_transcription_job_state_next_attempt",
        table_name="alpha_transcription_jobs",
    )
    op.drop_index(
        "ix_alpha_transcription_job_workspace_state_created",
        table_name="alpha_transcription_jobs",
    )
    op.drop_table("alpha_transcription_jobs")
