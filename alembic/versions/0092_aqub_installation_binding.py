"""Add durable AQUB Telegram installation binding.

Revision ID: 0092_aqub_installation_binding
Revises: 0091_beta_universal_material
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_aqub_installation_binding"
down_revision: str | Sequence[str] | None = "0091_beta_universal_material"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aqub_installation_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bot_key", sa.String(64), nullable=False),
        sa.Column("group_chat_id", sa.String(64), nullable=False),
        sa.Column("alpha_user_id", sa.String(64), nullable=False),
        sa.Column("evidence_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
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
            ["evidence_receipt_id"],
            ["telegram_receipts.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bot_key", name="uq_aqub_installation_binding_bot_key"),
        sa.UniqueConstraint(
            "evidence_receipt_id",
            name="uq_aqub_installation_binding_evidence_receipt",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    evidence = connection.execute(
        sa.text("SELECT count(*) FROM aqub_installation_bindings")
    ).scalar_one()
    if evidence:
        raise RuntimeError("aqub_installation_binding_evidence_blocks_downgrade")
    op.drop_table("aqub_installation_bindings")
