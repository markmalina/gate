"""Version Business text parity after account-relative direction correction.

Revision ID: 0037_direction_parity_v2
Revises: 0036_business_text_parity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_direction_parity_v2"
down_revision: str | None = "0036_business_text_parity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_business_text_parity_evidence",
        sa.Column(
            "direction_policy_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_business_text_parity_direction_policy_version",
        "telegram_business_text_parity_evidence",
        "direction_policy_version > 0",
    )
    op.alter_column(
        "telegram_business_text_parity_evidence",
        "direction_policy_version",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=sa.text("2"),
    )
    op.drop_index(
        "ix_business_text_parity_workspace_account_state",
        table_name="telegram_business_text_parity_evidence",
    )
    op.create_index(
        "ix_business_text_parity_workspace_account_state",
        "telegram_business_text_parity_evidence",
        ["workspace_id", "account_id", "direction_policy_version", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_text_parity_workspace_account_state",
        table_name="telegram_business_text_parity_evidence",
    )
    op.create_index(
        "ix_business_text_parity_workspace_account_state",
        "telegram_business_text_parity_evidence",
        ["workspace_id", "account_id", "state"],
    )
    op.drop_constraint(
        "ck_business_text_parity_direction_policy_version",
        "telegram_business_text_parity_evidence",
        type_="check",
    )
    op.drop_column(
        "telegram_business_text_parity_evidence",
        "direction_policy_version",
    )
