"""Add durable asynchronous AQUB Food media delivery state.

Revision ID: 0101_aqub_food_media_async
Revises: 0100_aqub_food_media
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0101_aqub_food_media_async"
down_revision: str | None = "0100_aqub_food_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_aqub_food_media_status",
        "aqub_food_media",
        type_="check",
    )
    op.drop_constraint(
        "ck_aqub_food_media_attempts",
        "aqub_food_media",
        type_="check",
    )
    op.add_column(
        "aqub_food_media",
        sa.Column("progress_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aqub_food_media",
        sa.Column("final_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_aqub_food_media_status",
        "aqub_food_media",
        "status IN ('queued','processing','processed','failed')",
    )
    op.create_check_constraint(
        "ck_aqub_food_media_attempts",
        "aqub_food_media",
        "attempt_count BETWEEN 0 AND 3",
    )
    op.create_index(
        "ix_aqub_food_media_worker",
        "aqub_food_media",
        ["status", "final_sent_at", "processing_started_at"],
        postgresql_where=sa.text("final_sent_at IS NULL"),
    )


def downgrade() -> None:
    queued = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM aqub_food_media WHERE status = 'queued'")
    )
    if queued:
        raise RuntimeError("aqub_food_media_queued_jobs_block_downgrade")
    op.drop_index("ix_aqub_food_media_worker", table_name="aqub_food_media")
    op.drop_constraint(
        "ck_aqub_food_media_attempts",
        "aqub_food_media",
        type_="check",
    )
    op.drop_constraint(
        "ck_aqub_food_media_status",
        "aqub_food_media",
        type_="check",
    )
    op.drop_column("aqub_food_media", "final_sent_at")
    op.drop_column("aqub_food_media", "progress_sent_at")
    op.create_check_constraint(
        "ck_aqub_food_media_attempts",
        "aqub_food_media",
        "attempt_count BETWEEN 1 AND 3",
    )
    op.create_check_constraint(
        "ck_aqub_food_media_status",
        "aqub_food_media",
        "status IN ('processing','processed','failed')",
    )
