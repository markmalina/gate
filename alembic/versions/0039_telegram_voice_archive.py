"""Persist bounded original Telegram Business voice audio.

Revision ID: 0039_voice_archive
Revises: 0038_business_text_cutover
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_voice_archive"
down_revision: str | None = "0038_business_text_cutover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_voice_archives",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column(
            "business_connection_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_telegram_voice_archives_direction",
        ),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_telegram_voice_archives_duration",
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 20971520",
            name="ck_telegram_voice_archives_size",
        ),
        sa.CheckConstraint(
            "length(sha256) = 64",
            name="ck_telegram_voice_archives_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["telegram_receipts.id"],
            name="fk_telegram_voice_archives_receipt_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_telegram_voice_archives_account_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_telegram_voice_archives_project_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "receipt_id",
            name="uq_telegram_voice_archives_receipt_id",
        ),
        sa.UniqueConstraint(
            "account_id",
            "business_connection_id",
            "chat_id",
            "telegram_message_id",
            name="uq_telegram_voice_archives_message_identity",
        ),
    )
    op.create_index(
        "ix_telegram_voice_archives_account_received",
        "telegram_voice_archives",
        ["account_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_voice_archives_account_received",
        table_name="telegram_voice_archives",
    )
    op.drop_table("telegram_voice_archives")
