"""Add one-call BECOME voice transcription accounting.

Revision ID: 0079_become_voice_transcription
Revises: 0078_become_voice_chain
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079_become_voice_transcription"
down_revision: str | None = "0078_become_voice_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTITLEMENT_KEY = "gate-become-voice-transcription-v1"
_SCOPE_KIND = "become_media_intake"
_SCOPE_REF = "telegram_become_group"
_GRANT_KEY = "become-investor-demo-transcription-v1"


def upgrade() -> None:
    op.create_table(
        "become_voice_transcription_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_intake_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_key", sa.String(length=128), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reservation_key", sa.String(length=128), nullable=False),
        sa.Column("period_key", sa.String(length=64), nullable=False),
        sa.Column("requested_microusd", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("provider_call_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_operation_ref", sa.String(length=255), nullable=True),
        sa.Column("staged_transcript", sa.Text(), nullable=True),
        sa.Column("transcript_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "provider_succeeded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("billed_quantity", sa.BigInteger(), nullable=True),
        sa.Column("actual_microusd", sa.BigInteger(), nullable=True),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
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
            "schema_name = 'gate.become-voice-transcription@1'",
            name="ck_become_voice_transcription_schema",
        ),
        sa.CheckConstraint(
            "state IN ('reserving', 'reserved', 'calling', "
            "'succeeded_unsettled', 'confirmed', 'rejected', 'unknown', 'blocked')",
            name="ck_become_voice_transcription_state",
        ),
        sa.CheckConstraint(
            f"grant_key = '{_GRANT_KEY}'",
            name="ck_become_voice_transcription_grant",
        ),
        sa.CheckConstraint(
            "requested_microusd > 0 AND "
            "(billed_quantity IS NULL OR billed_quantity >= 0)",
            name="ck_become_voice_transcription_amounts",
        ),
        sa.CheckConstraint(
            "actual_microusd IS NULL OR "
            "(actual_microusd >= 0 AND actual_microusd <= requested_microusd)",
            name="ck_become_voice_transcription_cost",
        ),
        sa.CheckConstraint(
            "(state IN ('reserving', 'reserved', 'blocked') "
            "AND provider_call_started_at IS NULL) OR "
            "(state IN ('calling', 'succeeded_unsettled', 'confirmed', "
            "'rejected', 'unknown') AND provider_call_started_at IS NOT NULL)",
            name="ck_become_voice_transcription_call_boundary",
        ),
        sa.CheckConstraint(
            "(state = 'succeeded_unsettled' "
            "AND staged_transcript IS NOT NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND provider_succeeded_at IS NOT NULL "
            "AND billed_quantity IS NOT NULL AND actual_microusd IS NOT NULL "
            "AND char_length(receipt_digest) = 64 AND reason_code IS NULL) OR "
            "(state = 'confirmed' "
            "AND staged_transcript IS NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND provider_succeeded_at IS NOT NULL "
            "AND billed_quantity IS NOT NULL AND actual_microusd IS NOT NULL "
            "AND char_length(receipt_digest) = 64 AND reason_code IS NULL) OR "
            "(state NOT IN ('succeeded_unsettled', 'confirmed') "
            "AND staged_transcript IS NULL AND transcript_digest IS NULL "
            "AND provider_succeeded_at IS NULL "
            "AND billed_quantity IS NULL AND actual_microusd IS NULL "
            "AND receipt_digest IS NULL)",
            name="ck_become_voice_transcription_success",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_voice_transcription_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            name="fk_become_voice_transcription_correlation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_intake_id"],
            ["become_media_intakes.id"],
            name="fk_become_voice_transcription_media",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correlation_id",
            name="uq_become_voice_transcription_correlation",
        ),
        sa.UniqueConstraint(
            "media_intake_id",
            name="uq_become_voice_transcription_media",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "grant_key",
            name="uq_become_voice_transcription_workspace_grant",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "reservation_key",
            name="uq_become_voice_transcription_workspace_reservation",
        ),
    )
    op.create_index(
        "ix_become_voice_transcription_workspace_state",
        "become_voice_transcription_attempts",
        ["workspace_id", "state"],
        unique=False,
    )
    op.execute(
        f"""
        INSERT INTO billing_entitlements (
            id, workspace_id, entitlement_key, product, scope_kind,
            scope_ref, plan_key, state, valid_from, valid_until,
            hard_limit_microusd
        )
        SELECT
            md5(workspaces.id::text || '-{_ENTITLEMENT_KEY}')::uuid,
            workspaces.id,
            '{_ENTITLEMENT_KEY}',
            'gate',
            '{_SCOPE_KIND}',
            '{_SCOPE_REF}',
            'gate-become-voice-one-call',
            'active',
            TIMESTAMPTZ '2026-08-01 00:00:00+00',
            NULL,
            100000
        FROM workspaces
        WHERE workspaces.is_active IS TRUE
          AND EXISTS (
              SELECT 1
              FROM transport_connectors
              WHERE transport_connectors.workspace_id = workspaces.id
                AND transport_connectors.connector_key = '{_SCOPE_REF}'
                AND transport_connectors.is_enabled IS TRUE
          )
        ON CONFLICT (workspace_id, entitlement_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM billing_entitlements
        WHERE entitlement_key = '{_ENTITLEMENT_KEY}'
          AND product = 'gate'
          AND scope_kind = '{_SCOPE_KIND}'
          AND scope_ref = '{_SCOPE_REF}'
        """
    )
    op.drop_index(
        "ix_become_voice_transcription_workspace_state",
        table_name="become_voice_transcription_attempts",
    )
    op.drop_table("become_voice_transcription_attempts")
