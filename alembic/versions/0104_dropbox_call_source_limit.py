"""Align the phone-call archive with the accepted Dropbox source ceiling.

Revision ID: 0104_dropbox_call_source_limit
Revises: 0103_aqub_food_routes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0104_dropbox_call_source_limit"
down_revision: str | None = "0103_aqub_food_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LIMIT = 25 * 1024 * 1024
_NEW_LIMIT = 100 * 1024 * 1024
_CONSTRAINT = "ck_phone_call_recordings_bounded_size"
_TABLE = "phone_call_recordings"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, f"size_bytes > 0 AND size_bytes <= {_NEW_LIMIT}"
    )


def downgrade() -> None:
    oversized = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM phone_call_recordings WHERE size_bytes > :old_limit"),
        {"old_limit": _OLD_LIMIT},
    )
    if oversized:
        raise RuntimeError("dropbox_call_source_limit_downgrade_blocked_by_oversized_rows")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, f"size_bytes > 0 AND size_bytes <= {_OLD_LIMIT}"
    )
