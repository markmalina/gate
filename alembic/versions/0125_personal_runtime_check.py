"""Record the last confirmed Telegram authorization separately from its lease."""

import sqlalchemy as sa
from alembic import op

revision = "0125_personal_runtime_check"
down_revision = "0124_personal_command_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personal_command_bindings", sa.Column("last_verified_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "personal_command_bindings", sa.Column("last_verified_generation", sa.Integer())
    )


def downgrade() -> None:
    op.drop_column("personal_command_bindings", "last_verified_generation")
    op.drop_column("personal_command_bindings", "last_verified_at")
