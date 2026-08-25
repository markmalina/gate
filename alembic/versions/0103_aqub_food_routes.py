"""Add default-inactive exact group+actor routes for isolated AQUB Food Cubes.

Revision ID: 0103_aqub_food_routes
Revises: 0102_aqub_general_routes
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0103_aqub_food_routes"
down_revision: str | None = "0102_aqub_general_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aqub_food_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bot_key", sa.String(length=64), nullable=False),
        sa.Column("group_chat_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence_receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'hold'"),
        ),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('hold','active','retired')",
            name="ck_aqub_food_route_status",
        ),
        sa.CheckConstraint(
            "(status = 'hold' AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(status = 'active' AND activated_at IS NOT NULL AND retired_at IS NULL) OR "
            "(status = 'retired' AND retired_at IS NOT NULL)",
            name="ck_aqub_food_route_lifecycle",
        ),
    )
    op.create_index(
        "uq_aqub_food_route_live_group",
        "aqub_food_routes",
        ["bot_key", "group_chat_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'retired'"),
    )


def downgrade() -> None:
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM aqub_food_routes"))
    if count:
        raise RuntimeError("aqub_food_route_evidence_blocks_downgrade")
    op.drop_index(
        "uq_aqub_food_route_live_group",
        table_name="aqub_food_routes",
    )
    op.drop_table("aqub_food_routes")
