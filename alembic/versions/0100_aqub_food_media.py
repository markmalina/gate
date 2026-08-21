"""Add owner-only AQUB Food raw media and normalized analysis ledger.

Revision ID: 0100_aqub_food_media
Revises: 0099_telegram_caption_backfill
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0100_aqub_food_media"
down_revision: str | None = "0099_telegram_caption_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aqub_food_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_data", sa.LargeBinary(), nullable=True),
        sa.Column("raw_purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("provider_model", sa.String(length=128), nullable=True),
        sa.Column("provider_request_ref", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "content_kind IN ('photo','voice')",
            name="ck_aqub_food_media_kind",
        ),
        sa.CheckConstraint(
            "status IN ('processing','processed','failed')",
            name="ck_aqub_food_media_status",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 1 AND 3",
            name="ck_aqub_food_media_attempts",
        ),
        sa.CheckConstraint(
            "char_length(source_sha256) = 64 OR source_sha256 IS NULL",
            name="ck_aqub_food_media_sha",
        ),
    )
    op.create_index(
        "ix_aqub_food_media_raw_purge",
        "aqub_food_media",
        ["raw_purge_after"],
        postgresql_where=sa.text("raw_data IS NOT NULL"),
    )


def downgrade() -> None:
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM aqub_food_media"))
    if count:
        raise RuntimeError("aqub_food_media_data_blocks_downgrade")
    op.drop_index("ix_aqub_food_media_raw_purge", table_name="aqub_food_media")
    op.drop_table("aqub_food_media")
