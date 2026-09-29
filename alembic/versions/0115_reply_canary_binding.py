"""Bind canary sequences durably so flag changes cannot revive their tails."""

import sqlalchemy as sa
from alembic import op

revision = "0115_reply_canary_binding"
down_revision = "0114_reply_continuity_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reply_sequences", sa.Column("canary_execution_id", sa.String(128)))


def downgrade() -> None:
    op.drop_column("reply_sequences", "canary_execution_id")
