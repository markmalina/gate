"""Add consent-bound BECOME media intake and retention.

Revision ID: 0076_become_media_intake
Revises: 0075_become_relations_execution
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_become_media_intake"
down_revision: str | None = "0075_become_relations_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_media_intakes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "registration_receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "consent_receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "relations_handoff_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("intake_key", sa.String(length=128), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("update_ref", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "file_unique_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_data", sa.LargeBinary(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_media_type", sa.String(length=128), nullable=True),
        sa.Column(
            "raw_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "raw_purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "transcript_state",
            sa.String(length=32),
            server_default="none",
            nullable=False,
        ),
        sa.Column("confirmed_transcript", sa.Text(), nullable=True),
        sa.Column("transcript_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "transcript_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "transcript_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "transcript_purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
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
            "schema_name = 'gate.become-media-intake@1'",
            name="ck_become_media_schema",
        ),
        sa.CheckConstraint(
            "kind IN ('voice', 'video')",
            name="ck_become_media_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending_download', 'archived', 'raw_purged')",
            name="ck_become_media_status",
        ),
        sa.CheckConstraint(
            "transcript_state IN ('none', 'confirmed', 'purged')",
            name="ck_become_media_transcript_state",
        ),
        sa.CheckConstraint(
            "char_length(file_unique_digest) = 64",
            name="ck_become_media_file_digest",
        ),
        sa.CheckConstraint(
            "declared_size_bytes IS NULL OR "
            "(declared_size_bytes > 0 AND declared_size_bytes <= 20000000)",
            name="ck_become_media_declared_size",
        ),
        sa.CheckConstraint(
            "("
            "status = 'pending_download' "
            "AND raw_data IS NULL "
            "AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL "
            "AND raw_media_type IS NULL "
            "AND raw_purged_at IS NULL"
            ") OR ("
            "status = 'archived' "
            "AND raw_data IS NOT NULL "
            "AND char_length(raw_sha256) = 64 "
            "AND raw_size_bytes > 0 "
            "AND raw_size_bytes <= 20000000 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_purged_at IS NULL"
            ") OR ("
            "status = 'raw_purged' "
            "AND raw_data IS NULL "
            "AND char_length(raw_sha256) = 64 "
            "AND raw_size_bytes > 0 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_purged_at IS NOT NULL"
            ")",
            name="ck_become_media_raw_state",
        ),
        sa.CheckConstraint(
            "("
            "transcript_state = 'none' "
            "AND confirmed_transcript IS NULL "
            "AND transcript_digest IS NULL "
            "AND transcript_confirmed_at IS NULL "
            "AND transcript_expires_at IS NULL "
            "AND transcript_purged_at IS NULL"
            ") OR ("
            "transcript_state = 'confirmed' "
            "AND confirmed_transcript IS NOT NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND transcript_confirmed_at IS NOT NULL "
            "AND transcript_expires_at IS NOT NULL "
            "AND transcript_purged_at IS NULL"
            ") OR ("
            "transcript_state = 'purged' "
            "AND confirmed_transcript IS NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND transcript_confirmed_at IS NOT NULL "
            "AND transcript_expires_at IS NOT NULL "
            "AND transcript_purged_at IS NOT NULL"
            ")",
            name="ck_become_media_transcript_retention",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_media_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_media_installation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_receipt_id"],
            ["telegram_receipts.id"],
            name="fk_become_media_source_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["registration_receipt_id"],
            ["become_controlled_intake_receipts.id"],
            name="fk_become_media_registration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["consent_receipt_id"],
            ["become_controlled_consent_receipts.id"],
            name="fk_become_media_consent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relations_handoff_id"],
            ["become_relations_consent_handoffs.id"],
            name="fk_become_media_relations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_receipt_id",
            name="uq_become_media_source_receipt",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "intake_key",
            name="uq_become_media_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_media_raw_expiry",
        "become_media_intakes",
        ["status", "raw_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_become_media_transcript_expiry",
        "become_media_intakes",
        ["transcript_state", "transcript_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_media_transcript_expiry",
        table_name="become_media_intakes",
    )
    op.drop_index(
        "ix_become_media_raw_expiry",
        table_name="become_media_intakes",
    )
    op.drop_table("become_media_intakes")
