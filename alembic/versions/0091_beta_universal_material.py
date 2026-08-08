"""Add provider-neutral Beta material jobs and phase delivery ledger.

Revision ID: 0091_beta_universal_material
Revises: 0090_beta_wedding_checkpoints_a
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0091_beta_universal_material"
down_revision: str | Sequence[str] | None = "0090_beta_wedding_checkpoints_a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_material_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_kind", sa.String(32), nullable=False),
        sa.Column("telegram_file_id", sa.String(255), nullable=False),
        sa.Column("sender_label", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source_file_sha256", sa.String(64), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("structured_text", sa.Text(), nullable=True),
        sa.Column("extracted_sha256", sa.String(64), nullable=True),
        sa.Column("structured_sha256", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["beta_space_events.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "media_kind IN ('voice','document','photo')",
            name="ck_beta_material_job_media_kind",
        ),
        sa.CheckConstraint(
            "state IN "
            "('receipt_pending','receipt_sent','processing','structured','failed')",
            name="ck_beta_material_job_state",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_beta_material_job_duration",
        ),
        sa.CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_beta_material_job_file_size",
        ),
        sa.UniqueConstraint("event_id", name="uq_beta_material_job_event"),
    )
    op.create_index(
        "ix_beta_material_job_scope_created",
        "beta_material_jobs",
        ["workspace_id", "binding_id", "created_at"],
    )
    op.create_table(
        "beta_material_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("candidate_text", sa.Text(), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["beta_material_jobs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "phase IN ('receipt','structured_result')",
            name="ck_beta_material_delivery_phase",
        ),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','unknown')",
            name="ck_beta_material_delivery_state",
        ),
        sa.CheckConstraint(
            "char_length(candidate_sha256) = 64",
            name="ck_beta_material_delivery_sha",
        ),
        sa.UniqueConstraint(
            "job_id", "phase", name="uq_beta_material_delivery_phase"
        ),
    )
    op.create_index(
        "ix_beta_material_delivery_state",
        "beta_material_deliveries",
        ["state", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    evidence = connection.execute(
        sa.text("SELECT count(*) FROM beta_material_jobs")
    ).scalar_one()
    if evidence:
        raise RuntimeError("beta_material_evidence_blocks_downgrade")
    op.drop_index(
        "ix_beta_material_delivery_state",
        table_name="beta_material_deliveries",
    )
    op.drop_table("beta_material_deliveries")
    op.drop_index(
        "ix_beta_material_job_scope_created",
        table_name="beta_material_jobs",
    )
    op.drop_table("beta_material_jobs")
