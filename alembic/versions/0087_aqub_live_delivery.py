"""Add AQUB live Telegram delivery ledger.

Revision ID: 0090_beta_wedding_checkpoints_a
Revises: 0090_beta_wedding_checkpoints
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0090_beta_wedding_checkpoints_a"
down_revision: str | Sequence[str] | None = "0090_beta_wedding_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aqub_live_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("chat_ref", sa.String(length=255), nullable=False),
        sa.Column("assignment_ref", sa.String(length=255), nullable=False),
        sa.Column("step_ref", sa.String(length=255), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','failed','unknown')",
            name="ck_aqub_live_delivery_state",
        ),
        sa.CheckConstraint(
            "char_length(request_sha256) = 64",
            name="ck_aqub_live_delivery_request_sha",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="uq_aqub_live_delivery_command"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_aqub_live_delivery_idempotency",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT count(*) FROM aqub_live_deliveries")
    ).scalar_one()
    if count:
        raise RuntimeError("aqub_live_delivery_data_blocks_downgrade")
    op.drop_table("aqub_live_deliveries")
