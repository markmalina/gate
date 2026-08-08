"""Add durable leased Beta Space work and dispatch recovery fields.

Revision ID: 0094_beta_durable_workers
Revises: 0093_beta_checkpoint_dispatch
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0094_beta_durable_workers"
down_revision: str | Sequence[str] | None = "0093_beta_checkpoint_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_space_work_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column(
            "receipt_id",
            sa.UUID(),
            sa.ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("beta_space_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "binding_id",
            sa.UUID(),
            sa.ForeignKey("beta_space_bindings.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("dedupe_key", name="uq_beta_space_work_item_dedupe"),
        sa.CheckConstraint(
            "kind IN ('candidate_consent','callback_delivery',"
            "'consent_delivery','reply','material')",
            name="ck_beta_space_work_item_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending','leased','retry','completed','failed','unknown')",
            name="ck_beta_space_work_item_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_beta_space_work_item_attempts",
        ),
        sa.CheckConstraint(
            "(state = 'leased' AND lease_expires_at IS NOT NULL) OR "
            "(state != 'leased' AND lease_expires_at IS NULL)",
            name="ck_beta_space_work_item_lease",
        ),
    )
    op.create_index(
        "ix_beta_space_work_item_due",
        "beta_space_work_items",
        ["state", "next_attempt_at", "lease_expires_at"],
    )
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
    )
    op.drop_constraint(
        "ck_beta_checkpoint_dispatch_intent_state",
        "beta_checkpoint_dispatch_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_beta_checkpoint_dispatch_intent_state",
        "beta_checkpoint_dispatch_intents",
        "state IN ('pending','sending','sent','failed','unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_beta_checkpoint_dispatch_intent_state",
        "beta_checkpoint_dispatch_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_beta_checkpoint_dispatch_intent_state",
        "beta_checkpoint_dispatch_intents",
        "state IN ('pending','sending','sent')",
    )
    op.drop_column("beta_checkpoint_dispatch_intents", "max_attempts")
    op.drop_column("beta_checkpoint_dispatch_intents", "settled_at")
    op.drop_column("beta_checkpoint_dispatch_intents", "lease_expires_at")
    op.drop_index("ix_beta_space_work_item_due", table_name="beta_space_work_items")
    op.drop_table("beta_space_work_items")
