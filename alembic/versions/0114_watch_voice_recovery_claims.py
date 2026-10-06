"""Permanent one-shot owner watch-note recovery journal; originals stay intact."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0114_watch_voice_recovery_claims"
down_revision: str | None = "0113_watch_voice_drain_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watch_voice_recovery_claims",
        sa.Column("note_id", sa.String(36), nullable=False),
        sa.Column("original_reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_error", sa.String(128), nullable=False),
        sa.Column("original_model", sa.String(64), nullable=False),
        sa.Column("original_attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorization_sha", sa.String(40), nullable=False),
        sa.Column("authorization_kind", sa.String(32), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("note_id"),
        sa.ForeignKeyConstraint(["note_id"], ["watch_voice_notes.id"]),
        sa.ForeignKeyConstraint(
            ["original_reservation_id"], ["billing_budget_reservations.id"]
        ),
        sa.CheckConstraint(
            "outcome IN ('claimed','transcribed','rejected','unknown')",
            name="ck_watch_voice_recovery_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("watch_voice_recovery_claims")
