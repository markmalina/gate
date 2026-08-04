"""Add bounded owner-only Slice 2 PROGRAM draft state.

Revision ID: 0085_become_pilot_slice2_program
Revises: 0084_become_pilot_slice1_review
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0085_become_pilot_slice2_program"
down_revision: str | None = "0084_become_pilot_slice1_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_pilot_program_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_transcript_version", sa.Integer(), nullable=False),
        sa.Column("source_transcript_digest", sa.String(length=64), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("callback_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_model", sa.String(length=128), nullable=True),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("approved_digest", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('reserved','calling','pending_display','presented',"
            "'awaiting_correction','approved','unknown','invalidated')",
            name="ck_become_program_state",
        ),
        sa.CheckConstraint(
            "latest_version >= 0", name="ck_become_program_latest_version"
        ),
        sa.CheckConstraint(
            "char_length(source_transcript_digest) = 64",
            name="ck_become_program_source_digest",
        ),
        sa.CheckConstraint(
            "(state = 'approved' AND approved_version = latest_version "
            "AND char_length(approved_digest) = 64 AND approved_at IS NOT NULL) "
            "OR (state != 'approved' AND approved_version IS NULL "
            "AND approved_digest IS NULL AND approved_at IS NULL)",
            name="ck_become_program_approval",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["transcript_review_id"],
            ["become_pilot_transcript_reviews.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["dialog_id"], ["dialogs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_review_id",
            "source_transcript_version",
            "source_transcript_digest",
            name="uq_become_program_source",
        ),
        sa.UniqueConstraint("callback_ref", name="uq_become_program_callback"),
    )
    op.create_index(
        "ix_become_program_workspace_state",
        "become_pilot_program_drafts",
        ["workspace_id", "state"],
    )

    op.create_table(
        "become_pilot_program_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("program_body", sa.Text(), nullable=True),
        sa.Column("program_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0", name="ck_become_program_version_positive"
        ),
        sa.CheckConstraint(
            "source IN ('provider','human_correction')",
            name="ck_become_program_version_source",
        ),
        sa.CheckConstraint(
            "char_length(program_digest) = 64",
            name="ck_become_program_version_digest",
        ),
        sa.CheckConstraint(
            "(program_body IS NOT NULL AND purged_at IS NULL) OR "
            "(program_body IS NULL AND purged_at IS NOT NULL)",
            name="ck_become_program_version_retention",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["become_pilot_program_drafts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id", "version", name="uq_become_program_version"
        ),
    )
    op.create_index(
        "ix_become_program_version_expiry",
        "become_pilot_program_versions",
        ["expires_at", "purged_at"],
    )

    op.create_table(
        "become_pilot_program_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("program_digest", sa.String(length=64), nullable=False),
        sa.Column("callback_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=64), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('presentation','redisplay','correction_prompt','confirmation')",
            name="ck_become_program_delivery_kind",
        ),
        sa.CheckConstraint(
            "version > 0 AND char_length(program_digest) = 64 "
            "AND char_length(request_digest) = 64",
            name="ck_become_program_delivery_binding",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending','reserved','sent','failed','unknown')",
            name="ck_become_program_delivery_outcome",
        ),
        sa.CheckConstraint(
            "(outcome IN ('pending','reserved') AND telegram_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(outcome = 'sent' AND telegram_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(outcome IN ('failed','unknown') AND telegram_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_become_program_delivery_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["become_pilot_program_drafts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "kind",
            "version",
            "program_digest",
            name="uq_become_program_delivery_operation",
        ),
        sa.UniqueConstraint(
            "request_digest", name="uq_become_program_delivery_request"
        ),
    )
    op.create_index(
        "ix_become_program_delivery_outcome",
        "become_pilot_program_deliveries",
        ["outcome", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_program_delivery_outcome",
        table_name="become_pilot_program_deliveries",
    )
    op.drop_table("become_pilot_program_deliveries")
    op.drop_index(
        "ix_become_program_version_expiry",
        table_name="become_pilot_program_versions",
    )
    op.drop_table("become_pilot_program_versions")
    op.drop_index(
        "ix_become_program_workspace_state",
        table_name="become_pilot_program_drafts",
    )
    op.drop_table("become_pilot_program_drafts")
