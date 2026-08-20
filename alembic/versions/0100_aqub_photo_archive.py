"""Add the default-off AQUB photo byte archive ledger.

Revision ID: 0100_aqub_photo_archive
Revises: 0099_telegram_caption_backfill
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0100_aqub_photo_archive"
down_revision: str | None = "0099_telegram_caption_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aqub_photo_archives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_unique_digest", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending_download",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", sa.LargeBinary(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_media_type", sa.String(length=64), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_purged_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending_download', 'processing', 'retry_scheduled', "
            "'archived', 'purged', 'dead_letter')",
            name="ck_aqub_photo_archives_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 5 "
            "AND lease_generation = attempt_count",
            name="ck_aqub_photo_archives_attempts",
        ),
        sa.CheckConstraint(
            "source_payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_aqub_photo_archives_source_digest",
        ),
        sa.CheckConstraint(
            "declared_size_bytes IS NULL OR "
            "(declared_size_bytes > 0 AND declared_size_bytes <= 20000000)",
            name="ck_aqub_photo_archives_declared_size",
        ),
        sa.CheckConstraint(
            "(status = 'dead_letter' "
            "AND file_unique_digest IS NULL "
            "AND width IS NULL AND height IS NULL "
            "AND declared_size_bytes IS NULL) OR ("
            "file_unique_digest IS NOT NULL "
            "AND file_unique_digest ~ '^[0-9a-f]{64}$' "
            "AND width IS NOT NULL AND width BETWEEN 1 AND 100000 "
            "AND height IS NOT NULL AND height BETWEEN 1 AND 100000)",
            name="ck_aqub_photo_archives_photo_identity",
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL AND last_error_at IS NULL) OR ("
            "last_error_code IS NOT NULL "
            "AND last_error_code ~ '^[a-z][a-z0-9_]{0,127}$' "
            "AND last_error_at IS NOT NULL)",
            name="ck_aqub_photo_archives_error_pair",
        ),
        sa.CheckConstraint(
            "(status = 'pending_download' "
            "AND attempt_count = 0 AND available_at IS NOT NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'processing' "
            "AND attempt_count BETWEEN 1 AND 5 "
            "AND available_at IS NULL "
            "AND lease_token IS NOT NULL AND locked_at IS NOT NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'retry_scheduled' "
            "AND attempt_count BETWEEN 1 AND 4 "
            "AND available_at IS NOT NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NOT NULL) "
            "OR (status IN ('archived', 'purged') "
            "AND attempt_count BETWEEN 1 AND 5 "
            "AND available_at IS NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'dead_letter' "
            "AND available_at IS NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NOT NULL)",
            name="ck_aqub_photo_archives_lease_state",
        ),
        sa.CheckConstraint(
            "(status IN ('pending_download', 'processing', 'retry_scheduled') "
            "AND raw_data IS NULL AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL AND raw_media_type IS NULL "
            "AND archived_at IS NULL AND purge_at IS NULL "
            "AND raw_purged_at IS NULL) "
            "OR (status = 'archived' "
            "AND raw_data IS NOT NULL AND raw_sha256 IS NOT NULL "
            "AND raw_sha256 ~ '^[0-9a-f]{64}$' "
            "AND raw_size_bytes IS NOT NULL "
            "AND raw_size_bytes BETWEEN 1 AND 20000000 "
            "AND raw_size_bytes = octet_length(raw_data) "
            "AND raw_media_type IS NOT NULL "
            "AND raw_media_type IN ('image/jpeg', 'image/png', 'image/webp') "
            "AND archived_at IS NOT NULL AND purge_at IS NOT NULL "
            "AND purge_at = archived_at + interval '7 days' "
            "AND raw_purged_at IS NULL) "
            "OR (status = 'purged' "
            "AND raw_data IS NULL AND raw_sha256 IS NOT NULL "
            "AND raw_sha256 ~ '^[0-9a-f]{64}$' "
            "AND raw_size_bytes IS NOT NULL "
            "AND raw_size_bytes BETWEEN 1 AND 20000000 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_media_type IN ('image/jpeg', 'image/png', 'image/webp') "
            "AND archived_at IS NOT NULL AND purge_at IS NOT NULL "
            "AND purge_at = archived_at + interval '7 days' "
            "AND raw_purged_at IS NOT NULL AND raw_purged_at >= purge_at) "
            "OR (status = 'dead_letter' "
            "AND raw_data IS NULL AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL AND raw_media_type IS NULL "
            "AND archived_at IS NULL AND purge_at IS NULL "
            "AND raw_purged_at IS NULL AND last_error_code IS NOT NULL)",
            name="ck_aqub_photo_archives_raw_state",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["telegram_receipts.id"],
            name="fk_aqub_photo_archives_receipt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["aqub_installation_bindings.id"],
            name="fk_aqub_photo_archives_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", name="uq_aqub_photo_archives_receipt"),
    )
    op.create_index(
        "ix_aqub_photo_archives_due",
        "aqub_photo_archives",
        ["available_at", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending_download', 'retry_scheduled') "
            "AND attempt_count < 5"
        ),
    )
    op.create_index(
        "ix_telegram_receipts_aqub_photo_discovery",
        "telegram_receipts",
        ["bot_key", "received_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status = 'processed' AND processed_at IS NOT NULL "
            "AND jsonb_typeof(payload->'message'->'photo') = 'array'"
        ),
    )
    op.create_index(
        "ix_aqub_photo_archives_stale",
        "aqub_photo_archives",
        ["locked_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_aqub_photo_archives_purge",
        "aqub_photo_archives",
        ["purge_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'archived'"),
    )


def downgrade() -> None:
    # CI-only reversibility. Production rollback leaves this additive table in place.
    op.drop_index(
        "ix_telegram_receipts_aqub_photo_discovery",
        table_name="telegram_receipts",
    )
    op.drop_index(
        "ix_aqub_photo_archives_purge",
        table_name="aqub_photo_archives",
    )
    op.drop_index(
        "ix_aqub_photo_archives_stale",
        table_name="aqub_photo_archives",
    )
    op.drop_index(
        "ix_aqub_photo_archives_due",
        table_name="aqub_photo_archives",
    )
    op.drop_table("aqub_photo_archives")
