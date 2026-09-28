"""Durable exact-scope canary attempt ledger; no activation or sends."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0113_reply_canary_budget"
down_revision = "0112_reply_sequence_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_canary_scopes",
        sa.Column("workspace_ref", sa.String(length=128), primary_key=True),
        sa.Column("execution_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("sequence_grants", JSONB(), nullable=False),
        sa.Column("notification_destination", sa.String(length=255), nullable=False),
        sa.Column("notification_template", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempts_reserved", sa.Integer(), nullable=False),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("state IN ('active','closed')"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 6"),
        sa.CheckConstraint("attempts_reserved >= 0 AND attempts_reserved <= max_attempts"),
    )
    op.create_table(
        "reply_canary_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_ref", sa.String(length=128), nullable=False),
        sa.Column("attempt_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("execution_id", sa.String(length=128), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_ref"], ["reply_canary_scopes.workspace_ref"]),
        sa.UniqueConstraint("workspace_ref", "attempt_key"),
    )


def downgrade() -> None:
    op.drop_table("reply_canary_attempts")
    op.drop_table("reply_canary_scopes")
