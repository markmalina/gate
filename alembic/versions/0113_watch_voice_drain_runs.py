"""Hash-only durable GitHub Actions watch-drain replay guard."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113_watch_voice_drain_runs"
down_revision: str | None = "0112_watch_voice_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watch_voice_cursors",
        sa.Column(
            "future_cutover_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_table(
        "watch_voice_drain_runs",
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("jwt_key", sa.String(64), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_key"),
        sa.UniqueConstraint("jwt_key"),
    )


def downgrade() -> None:
    op.drop_table("watch_voice_drain_runs")
    op.drop_column("watch_voice_cursors", "future_cutover_at")
