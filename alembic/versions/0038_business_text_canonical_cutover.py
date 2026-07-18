"""Link the current human inbox read model to canonical Business messages.

Revision ID: 0038_business_text_cutover
Revises: 0037_direction_parity_v2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_business_text_cutover"
down_revision: str | None = "0037_direction_parity_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "canonical_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_messages_canonical_message_id",
        "messages",
        "canonical_messages",
        ["canonical_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_messages_canonical_message_id",
        "messages",
        ["canonical_message_id"],
    )
    op.create_index(
        "ix_messages_canonical_message_id",
        "messages",
        ["canonical_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_canonical_message_id", table_name="messages")
    op.drop_constraint(
        "uq_messages_canonical_message_id",
        "messages",
        type_="unique",
    )
    op.drop_constraint(
        "fk_messages_canonical_message_id",
        "messages",
        type_="foreignkey",
    )
    op.drop_column("messages", "canonical_message_id")
