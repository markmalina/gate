"""Allow atomic processing claims for stored phone calls.

Revision ID: 0060_phone_call_processing_status
Revises: 0059_merge_audio_beta_heads
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_phone_call_processing_status"
down_revision: str | None = "0059_merge_audio_beta_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_phone_call_recordings_transcription_status"
_TABLE = "phone_call_recordings"
_OLD_STATUSES = ("received", "disabled", "rejected", "transcribed", "unknown")
_NEW_STATUSES = (*_OLD_STATUSES, "processing")


def _status_check(statuses: tuple[str, ...]) -> str:
    values = ", ".join(f"'{status}'" for status in statuses)
    return f"transcription_status IN ({values})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        _status_check(_NEW_STATUSES),
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE phone_call_recordings "
            "SET transcription_status = 'received', "
            "transcription_reason_code = NULL, "
            "transcribed_at = NULL "
            "WHERE transcription_status = 'processing'"
        )
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        _status_check(_OLD_STATUSES),
    )
