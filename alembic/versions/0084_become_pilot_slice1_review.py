"""Add bounded BECOME Pilot transcript human review.

Revision ID: 0084_become_pilot_slice1_review
Revises: 0083_become_quest_corrective_m
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084_become_pilot_slice1_review"
down_revision: str | None = "0083_become_quest_corrective_m"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_GRANT = "become-investor-demo-transcription-v1"
_SLICE1_GRANT = "become-pilot-slice1-transcription-v2"


def upgrade() -> None:
    op.drop_constraint(
        "ck_become_voice_transcription_grant",
        "become_voice_transcription_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_grant",
        "become_voice_transcription_attempts",
        (
            "grant_key IN "
            f"('{_LEGACY_GRANT}', '{_SLICE1_GRANT}')"
        ),
    )
    op.add_column(
        "become_voice_transcription_attempts",
        sa.Column(
            "staged_transcript_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE become_voice_transcription_attempts
        SET staged_transcript_expires_at =
            provider_succeeded_at + INTERVAL '30 days'
        WHERE state IN ('succeeded_unsettled', 'confirmed')
          AND provider_succeeded_at IS NOT NULL
        """
    )
    for constraint in (
        "ck_become_voice_transcription_state",
        "ck_become_voice_transcription_call_boundary",
        "ck_become_voice_transcription_success",
    ):
        op.drop_constraint(
            constraint,
            "become_voice_transcription_attempts",
            type_="check",
        )
    op.create_check_constraint(
        "ck_become_voice_transcription_state",
        "become_voice_transcription_attempts",
        "state IN ('reserving', 'reserved', 'calling', "
        "'succeeded_unsettled', 'confirmed', 'rejected', "
        "'unknown', 'blocked', 'reconciliation_required')",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_call_boundary",
        "become_voice_transcription_attempts",
        "(state IN ('reserving', 'reserved', 'blocked') "
        "AND provider_call_started_at IS NULL) OR "
        "(state IN ('calling', 'succeeded_unsettled', 'confirmed', "
        "'rejected', 'unknown', 'reconciliation_required') "
        "AND provider_call_started_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_success",
        "become_voice_transcription_attempts",
        "(state = 'succeeded_unsettled' "
        "AND staged_transcript IS NOT NULL "
        "AND staged_transcript_expires_at IS NOT NULL "
        "AND char_length(transcript_digest) = 64 "
        "AND provider_succeeded_at IS NOT NULL "
        "AND staged_transcript_expires_at > provider_succeeded_at "
        "AND billed_quantity IS NOT NULL "
        "AND actual_microusd IS NOT NULL "
        "AND char_length(receipt_digest) = 64 "
        "AND reason_code IS NULL) OR "
        "(state = 'confirmed' "
        "AND staged_transcript IS NULL "
        "AND staged_transcript_expires_at IS NOT NULL "
        "AND char_length(transcript_digest) = 64 "
        "AND provider_succeeded_at IS NOT NULL "
        "AND billed_quantity IS NOT NULL "
        "AND actual_microusd IS NOT NULL "
        "AND char_length(receipt_digest) = 64 "
        "AND reason_code IS NULL) OR "
        "(state = 'reconciliation_required' "
        "AND staged_transcript IS NULL "
        "AND staged_transcript_expires_at IS NOT NULL "
        "AND char_length(transcript_digest) = 64 "
        "AND provider_succeeded_at IS NOT NULL "
        "AND billed_quantity IS NOT NULL "
        "AND actual_microusd IS NOT NULL "
        "AND char_length(receipt_digest) = 64 "
        "AND reason_code IS NOT NULL) OR "
        "(state NOT IN ('succeeded_unsettled', 'confirmed', "
        "'reconciliation_required') "
        "AND staged_transcript IS NULL "
        "AND staged_transcript_expires_at IS NULL "
        "AND transcript_digest IS NULL "
        "AND provider_succeeded_at IS NULL "
        "AND billed_quantity IS NULL "
        "AND actual_microusd IS NULL "
        "AND receipt_digest IS NULL)",
    )

    op.create_table(
        "become_pilot_transcript_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_intake_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("callback_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("approved_digest", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending_display', 'presented', "
            "'awaiting_correction', 'approved')",
            name="ck_become_pilot_review_state",
        ),
        sa.CheckConstraint(
            "latest_version > 0",
            name="ck_become_pilot_review_latest_version",
        ),
        sa.CheckConstraint(
            "(state = 'approved' "
            "AND approved_version = latest_version "
            "AND char_length(approved_digest) = 64 "
            "AND approved_at IS NOT NULL) OR "
            "(state != 'approved' "
            "AND approved_version IS NULL "
            "AND approved_digest IS NULL "
            "AND approved_at IS NULL)",
            name="ck_become_pilot_review_approval",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_pilot_review_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            name="fk_become_pilot_review_correlation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_intake_id"],
            ["become_media_intakes.id"],
            name="fk_become_pilot_review_media",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["become_voice_transcription_attempts.id"],
            name="fk_become_pilot_review_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id"],
            ["dialogs.id"],
            name="fk_become_pilot_review_dialog",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_become_pilot_review_attempt",
        ),
        sa.UniqueConstraint(
            "correlation_id",
            name="uq_become_pilot_review_correlation",
        ),
        sa.UniqueConstraint(
            "media_intake_id",
            name="uq_become_pilot_review_media",
        ),
        sa.UniqueConstraint(
            "callback_ref",
            name="uq_become_pilot_review_callback",
        ),
    )
    op.create_index(
        "ix_become_pilot_review_workspace_state",
        "become_pilot_transcript_reviews",
        ["workspace_id", "state"],
        unique=False,
    )

    op.create_table(
        "become_pilot_transcript_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("transcript_body", sa.Text(), nullable=True),
        sa.Column("transcript_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name="ck_become_pilot_version_positive",
        ),
        sa.CheckConstraint(
            "source IN ('provider', 'human_correction')",
            name="ck_become_pilot_version_source",
        ),
        sa.CheckConstraint(
            "char_length(transcript_digest) = 64",
            name="ck_become_pilot_version_digest",
        ),
        sa.CheckConstraint(
            "(transcript_body IS NOT NULL AND purged_at IS NULL) "
            "OR (transcript_body IS NULL AND purged_at IS NOT NULL)",
            name="ck_become_pilot_version_retention",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["become_pilot_transcript_reviews.id"],
            name="fk_become_pilot_version_review",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_id",
            "version",
            name="uq_become_pilot_version",
        ),
    )
    op.create_index(
        "ix_become_pilot_version_expiry",
        "become_pilot_transcript_versions",
        ["expires_at", "purged_at"],
        unique=False,
    )

    op.create_table(
        "become_pilot_transcript_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "retry_of_delivery_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "attempt_no",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("transcript_digest", sa.String(length=64), nullable=False),
        sa.Column("callback_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=64), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('presentation', 'redisplay', "
            "'correction_prompt', 'confirmation')",
            name="ck_become_pilot_delivery_kind",
        ),
        sa.CheckConstraint(
            "version > 0 AND char_length(transcript_digest) = 64 "
            "AND char_length(request_digest) = 64",
            name="ck_become_pilot_delivery_binding",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'reserved', 'sent', 'failed', 'unknown')",
            name="ck_become_pilot_delivery_outcome",
        ),
        sa.CheckConstraint(
            "(outcome IN ('pending', 'reserved') "
            "AND telegram_message_id IS NULL AND settled_at IS NULL) OR "
            "(outcome = 'sent' "
            "AND telegram_message_id IS NOT NULL AND settled_at IS NOT NULL) OR "
            "(outcome IN ('failed', 'unknown') "
            "AND telegram_message_id IS NULL AND settled_at IS NOT NULL)",
            name="ck_become_pilot_delivery_settlement",
        ),
        sa.CheckConstraint(
            "(attempt_no = 1 AND retry_of_delivery_id IS NULL) OR "
            "(attempt_no = 2 AND retry_of_delivery_id IS NOT NULL)",
            name="ck_become_pilot_delivery_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["become_pilot_transcript_reviews.id"],
            name="fk_become_pilot_delivery_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_delivery_id"],
            ["become_pilot_transcript_deliveries.id"],
            name="fk_become_pilot_delivery_retry_of",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_id",
            "kind",
            "version",
            "transcript_digest",
            "attempt_no",
            name="uq_become_pilot_delivery_operation",
        ),
        sa.UniqueConstraint(
            "request_digest",
            name="uq_become_pilot_delivery_request",
        ),
    )
    op.create_index(
        "ix_become_pilot_delivery_outcome_created",
        "become_pilot_transcript_deliveries",
        ["outcome", "created_at"],
        unique=False,
    )
    op.create_table(
        "become_pilot_delivery_reconciliations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('sent_confirmed', 'failed_confirmed', "
            "'retry_authorized')",
            name="ck_become_pilot_delivery_reconciliation_action",
        ),
        sa.CheckConstraint(
            "char_length(request_digest) = 64 "
            "AND char_length(evidence_digest) = 64",
            name="ck_become_pilot_delivery_reconciliation_digests",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["become_pilot_transcript_deliveries.id"],
            name="fk_become_pilot_delivery_reconciliation_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id",
            "action",
            name="uq_become_pilot_delivery_reconciliation_action",
        ),
        sa.UniqueConstraint(
            "request_digest",
            name="uq_become_pilot_delivery_reconciliation_request",
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM become_pilot_delivery_reconciliations
          ) OR EXISTS (
            SELECT 1 FROM become_pilot_transcript_deliveries
          ) OR EXISTS (
            SELECT 1 FROM become_pilot_transcript_versions
          ) OR EXISTS (
            SELECT 1 FROM become_pilot_transcript_reviews
          ) THEN
            RAISE EXCEPTION
              'refusing to delete non-empty BECOME Pilot review ledger';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM become_voice_transcription_attempts
            WHERE grant_key = 'become-pilot-slice1-transcription-v2'
          ) THEN
            RAISE EXCEPTION
              'refusing to remove active BECOME Pilot transcription grant';
          END IF;
        END
        $$;
        """
    )
    op.drop_table("become_pilot_delivery_reconciliations")
    op.drop_index(
        "ix_become_pilot_delivery_outcome_created",
        table_name="become_pilot_transcript_deliveries",
    )
    op.drop_table("become_pilot_transcript_deliveries")
    op.drop_index(
        "ix_become_pilot_version_expiry",
        table_name="become_pilot_transcript_versions",
    )
    op.drop_table("become_pilot_transcript_versions")
    op.drop_index(
        "ix_become_pilot_review_workspace_state",
        table_name="become_pilot_transcript_reviews",
    )
    op.drop_table("become_pilot_transcript_reviews")
    for constraint in (
        "ck_become_voice_transcription_success",
        "ck_become_voice_transcription_call_boundary",
        "ck_become_voice_transcription_state",
    ):
        op.drop_constraint(
            constraint,
            "become_voice_transcription_attempts",
            type_="check",
        )
    op.drop_column(
        "become_voice_transcription_attempts",
        "staged_transcript_expires_at",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_state",
        "become_voice_transcription_attempts",
        "state IN ('reserving', 'reserved', 'calling', "
        "'succeeded_unsettled', 'confirmed', 'rejected', "
        "'unknown', 'blocked')",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_call_boundary",
        "become_voice_transcription_attempts",
        "(state IN ('reserving', 'reserved', 'blocked') "
        "AND provider_call_started_at IS NULL) OR "
        "(state IN ('calling', 'succeeded_unsettled', 'confirmed', "
        "'rejected', 'unknown') "
        "AND provider_call_started_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_success",
        "become_voice_transcription_attempts",
        "(state = 'succeeded_unsettled' "
        "AND staged_transcript IS NOT NULL "
        "AND char_length(transcript_digest) = 64 "
        "AND provider_succeeded_at IS NOT NULL "
        "AND billed_quantity IS NOT NULL "
        "AND actual_microusd IS NOT NULL "
        "AND char_length(receipt_digest) = 64 "
        "AND reason_code IS NULL) OR "
        "(state = 'confirmed' "
        "AND staged_transcript IS NULL "
        "AND char_length(transcript_digest) = 64 "
        "AND provider_succeeded_at IS NOT NULL "
        "AND billed_quantity IS NOT NULL "
        "AND actual_microusd IS NOT NULL "
        "AND char_length(receipt_digest) = 64 "
        "AND reason_code IS NULL) OR "
        "(state NOT IN ('succeeded_unsettled', 'confirmed') "
        "AND staged_transcript IS NULL "
        "AND transcript_digest IS NULL "
        "AND provider_succeeded_at IS NULL "
        "AND billed_quantity IS NULL "
        "AND actual_microusd IS NULL "
        "AND receipt_digest IS NULL)",
    )
    op.drop_constraint(
        "ck_become_voice_transcription_grant",
        "become_voice_transcription_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_become_voice_transcription_grant",
        "become_voice_transcription_attempts",
        f"grant_key = '{_LEGACY_GRANT}'",
    )
