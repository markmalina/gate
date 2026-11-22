"""Add a shared writer fence and timestamps for durable Watch attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0118_watch_early_writer"
down_revision = "0117_watch_early_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "watch_early_cursors",
        sa.Column("writer_mode", sa.String(16), nullable=False, server_default="legacy_actions"),
    )
    op.add_column(
        "watch_early_cursors",
        sa.Column("writer_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("watch_early_cards", sa.Column("attempt_started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("watch_early_cards", "attempt_started_at")
    op.drop_column("watch_early_cursors", "writer_generation")
    op.drop_column("watch_early_cursors", "writer_mode")
