"""Add durable coalesced Beta checkpoint dispatch intents.

Revision ID: 0093_beta_checkpoint_dispatch
Revises: 0092_aqub_installation_binding
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0093_beta_checkpoint_dispatch"
down_revision: str | Sequence[str] | None = "0092_aqub_installation_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_checkpoint_dispatch_intents",
        sa.Column("space_id", sa.String(128), primary_key=True),
        sa.Column("accepted_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "dispatched_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
            "state IN ('pending','sending','sent')",
            name="ck_beta_checkpoint_dispatch_intent_state",
        ),
        sa.CheckConstraint(
            "accepted_sequence >= 1",
            name="ck_beta_checkpoint_dispatch_intent_sequence",
        ),
        sa.CheckConstraint(
            "dispatched_sequence >= 0 AND dispatched_sequence <= accepted_sequence",
            name="ck_beta_checkpoint_dispatch_intent_dispatched",
        ),
    )
    op.create_index(
        "ix_beta_checkpoint_dispatch_intent_due",
        "beta_checkpoint_dispatch_intents",
        ["state", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_beta_checkpoint_dispatch_intent_due",
        table_name="beta_checkpoint_dispatch_intents",
    )
    op.drop_table("beta_checkpoint_dispatch_intents")
