"""Add durable payload-free Telegram queue incidents.

Revision ID: 0015_telegram_queue_incidents
Revises: 0014_telegram_receipt_outbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_telegram_queue_incidents"
down_revision: str | None = "0014_telegram_receipt_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_queue_incidents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_channel", sa.String(length=128), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_telegram_queue_incidents_status_observed",
        "telegram_queue_incidents",
        ["status", "last_observed_at"],
    )
    op.create_index(
        "uq_telegram_queue_incidents_single_active",
        "telegram_queue_incidents",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('open', 'notified', 'acknowledged')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_telegram_queue_incidents_single_active",
        table_name="telegram_queue_incidents",
    )
    op.drop_index(
        "ix_telegram_queue_incidents_status_observed",
        table_name="telegram_queue_incidents",
    )
    op.drop_table("telegram_queue_incidents")
