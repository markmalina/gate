"""Add durable generations to coalesced Beta checkpoint wake-ups.

Revision ID: 0095_beta_checkpoint_wake_generation
Revises: 0094_beta_durable_workers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0095_beta_checkpoint_wake_generation"
down_revision: str | Sequence[str] | None = "0094_beta_durable_workers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column(
            "wake_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column(
            "dispatched_wake_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        "UPDATE beta_checkpoint_dispatch_intents "
        "SET dispatched_wake_generation = 1 "
        "WHERE state IN ('sent', 'failed', 'unknown')"
    )
    op.create_check_constraint(
        "ck_beta_checkpoint_dispatch_intent_wake_generation",
        "beta_checkpoint_dispatch_intents",
        "wake_generation >= 1 AND dispatched_wake_generation >= 0 "
        "AND dispatched_wake_generation <= wake_generation",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_beta_checkpoint_dispatch_intent_wake_generation",
        "beta_checkpoint_dispatch_intents",
        type_="check",
    )
    op.drop_column(
        "beta_checkpoint_dispatch_intents",
        "dispatched_wake_generation",
    )
    op.drop_column("beta_checkpoint_dispatch_intents", "wake_generation")
