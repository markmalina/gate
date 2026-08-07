"""Add durable Wedding voice jobs and phase-keyed delivery ledger.

Revision ID: 0089_beta_wedding_voice_phases
Revises: 0088_beta_wedding_replies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089_beta_wedding_voice_phases"
down_revision: str | None = "0088_beta_wedding_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_wedding_voice_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_file_id", sa.String(255), nullable=False),
        sa.Column("sender_label", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("structured_text", sa.Text(), nullable=True),
        sa.Column("transcript_sha256", sa.String(64), nullable=True),
        sa.Column("structured_sha256", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
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
            "state IN ('receipt_pending','receipt_sent','transcribing','structured','failed')",
            name="ck_beta_wedding_voice_job_state",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_beta_wedding_voice_job_duration",
        ),
        sa.UniqueConstraint("event_id", name="uq_beta_wedding_voice_job_event"),
    )
    op.create_table(
        "beta_wedding_voice_deliveries",
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
            ["job_id"], ["beta_wedding_voice_jobs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "phase IN ('receipt','structured_result')",
            name="ck_beta_wedding_voice_delivery_phase",
        ),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','unknown')",
            name="ck_beta_wedding_voice_delivery_state",
        ),
        sa.CheckConstraint(
            "char_length(candidate_sha256) = 64",
            name="ck_beta_wedding_voice_delivery_sha",
        ),
        sa.UniqueConstraint(
            "job_id", "phase", name="uq_beta_wedding_voice_delivery_phase"
        ),
    )
    op.create_index(
        "ix_beta_wedding_voice_delivery_state",
        "beta_wedding_voice_deliveries",
        ["state", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT count(*) FROM beta_wedding_voice_jobs")
    ).scalar_one():
        raise RuntimeError("beta_wedding_voice_evidence_blocks_downgrade")
    op.drop_index(
        "ix_beta_wedding_voice_delivery_state",
        table_name="beta_wedding_voice_deliveries",
    )
    op.drop_table("beta_wedding_voice_deliveries")
    op.drop_table("beta_wedding_voice_jobs")
