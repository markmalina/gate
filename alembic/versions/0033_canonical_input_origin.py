"""Record canonical Telegram text origin.

Revision ID: 0033_canonical_input_origin
Revises: 0032_gate_voice_entitlement
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_canonical_input_origin"
down_revision: str | None = "0032_gate_voice_entitlement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a bounded origin to existing and future canonical messages."""

    op.add_column(
        "canonical_messages",
        sa.Column(
            "input_origin",
            sa.String(length=64),
            nullable=False,
            server_default="telegram_text",
        ),
    )
    op.create_check_constraint(
        "ck_canonical_messages_input_origin",
        "canonical_messages",
        "input_origin IN ('telegram_text', 'telegram_voice_transcription')",
    )


def downgrade() -> None:
    """Remove only the canonical input-origin field."""

    op.drop_constraint(
        "ck_canonical_messages_input_origin",
        "canonical_messages",
        type_="check",
    )
    op.drop_column("canonical_messages", "input_origin")
