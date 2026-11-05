"""Serialize accepted dialog changes with snapshot-bound reply admission."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116_reply_dialog_versions"
down_revision: str | None = "0115_watch_voice_post_payment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reply_dialog_versions",
        sa.Column("business_connection_id", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.String(255), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("business_connection_id", "chat_id"),
        sa.CheckConstraint("revision >= 0", name="ck_reply_dialog_version_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("reply_dialog_versions")
