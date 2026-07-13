"""Normalize Mark's accounts to the OAuth workspace identity.

Revision ID: 0012_mark_workspace_owner
Revises: 0011_confirmed_account_routes
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_mark_workspace_owner"
down_revision: str | None = "0011_confirmed_account_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MARK_WORKSPACE_ID = "mark-personal"
MARK_ACCOUNT_IDS = (
    "0d8e025f-323d-5e86-a44c-a620f690ae4f",
    "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2",
)


def upgrade() -> None:
    account_ids = ", ".join(f"'{account_id}'::uuid" for account_id in MARK_ACCOUNT_IDS)
    op.execute(
        f"""
        UPDATE accounts
        SET owner = '{MARK_WORKSPACE_ID}'
        WHERE id IN ({account_ids})
          AND owner IS DISTINCT FROM '{MARK_WORKSPACE_ID}'
        """
    )


def downgrade() -> None:
    # The prior free-form owner value is not an authorization boundary and
    # cannot be restored safely.
    pass
