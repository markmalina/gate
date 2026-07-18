"""Persist content-free Telegram Business text parity evidence.

Revision ID: 0036_business_text_parity
Revises: 0035_alpha_rate_limit_deferral
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_business_text_parity"
down_revision: str | None = "0035_alpha_rate_limit_deferral"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_business_text_parity_evidence",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "canonical_message_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("mismatch_code", sa.String(length=128), nullable=True),
        sa.Column(
            "observation_count", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "first_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('matched', 'pending', 'mismatch')",
            name="ck_business_text_parity_state",
        ),
        sa.CheckConstraint(
            "(state = 'matched' AND mismatch_code IS NULL) OR "
            "(state = 'pending' AND mismatch_code = "
            "'telegram_business_text_parity_legacy_pending') OR "
            "(state = 'mismatch' AND mismatch_code IN ("
            "'telegram_business_text_parity_account_mismatch', "
            "'telegram_business_text_parity_direction_mismatch', "
            "'telegram_business_text_parity_dialog_mismatch', "
            "'telegram_business_text_parity_text_mismatch', "
            "'telegram_business_text_parity_reply_task_mismatch', "
            "'telegram_business_text_parity_legacy_cardinality', "
            "'telegram_business_text_parity_legacy_invalid'))",
            name="ck_business_text_parity_code_state",
        ),
        sa.CheckConstraint(
            "observation_count > 0",
            name="ck_business_text_parity_observation_count",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_business_text_parity_message_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "canonical_message_id",
            name="uq_business_text_parity_canonical_message",
        ),
    )
    op.create_index(
        "ix_business_text_parity_workspace_account_state",
        "telegram_business_text_parity_evidence",
        ["workspace_id", "account_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_text_parity_workspace_account_state",
        table_name="telegram_business_text_parity_evidence",
    )
    op.drop_table("telegram_business_text_parity_evidence")
