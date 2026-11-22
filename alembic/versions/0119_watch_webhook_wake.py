"""Persist coalesced Watch webhook signals and the card's scan trigger time."""

import sqlalchemy as sa
from alembic import op

revision = "0119_watch_webhook_wake"
down_revision = "0118_watch_early_writer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("wake_generation", "wake_processed_generation"):
        op.add_column(
            "watch_early_cursors",
            sa.Column(column, sa.BigInteger(), nullable=False, server_default="0"),
        )
    for table in ("watch_early_cursors", "watch_early_cards"):
        op.add_column(table, sa.Column("wake_received_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for table in ("watch_early_cards", "watch_early_cursors"):
        op.drop_column(table, "wake_received_at")
    op.drop_column("watch_early_cursors", "wake_processed_generation")
    op.drop_column("watch_early_cursors", "wake_generation")
