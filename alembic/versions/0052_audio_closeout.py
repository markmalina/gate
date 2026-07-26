"""Close out bounded audio transcription, recovery and summary storage.

Revision ID: 0052_audio_closeout
Revises: 0051_beta_alexander_model
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_audio_closeout"
down_revision: str | None = "0051_beta_alexander_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LIMIT = 20 * 1024 * 1024
_NEW_LIMIT = 25 * 1024 * 1024
_RECOVERY_ARCHIVE_IDS = (
    "e24e93a6-10ef-4056-bff4-f2626b075ba6",
    "84d48642-da88-4ace-9d62-bc855df5e78d",
    "cf4e6eb0-f9dc-4bb6-a610-10ebc37a265c",
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_telegram_voice_archives_size",
        "telegram_voice_archives",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_voice_archives_size",
        "telegram_voice_archives",
        f"size_bytes > 0 AND size_bytes <= {_NEW_LIMIT}",
    )
    op.drop_constraint(
        "ck_phone_call_recordings_bounded_size",
        "phone_call_recordings",
        type_="check",
    )
    op.create_check_constraint(
        "ck_phone_call_recordings_bounded_size",
        "phone_call_recordings",
        f"size_bytes > 0 AND size_bytes <= {_NEW_LIMIT}",
    )
    op.add_column(
        "dropbox_call_inbox_states",
        sa.Column(
            "backfill_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "telegram_voice_archives",
        sa.Column(
            "transcription_recovery_authorized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    for table in ("telegram_voice_archives", "phone_call_recordings"):
        op.add_column(
            table,
            sa.Column("summary_status", sa.String(length=16), nullable=True),
        )
        op.add_column(table, sa.Column("summary_text", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column("summary_model", sa.String(length=64), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("summary_reason_code", sa.String(length=128), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
        )

    bind = op.get_bind()
    for archive_id in _RECOVERY_ARCHIVE_IDS:
        bind.execute(
            sa.text(
                "UPDATE telegram_voice_archives "
                "SET transcription_recovery_authorized_at = now() "
                "WHERE id = CAST(:archive_id AS uuid) "
                "AND (transcription_status IS NULL "
                "OR transcription_status = 'unknown')"
            ),
            {"archive_id": archive_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    oversized = bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM telegram_voice_archives "
            " WHERE size_bytes > :old_limit) + "
            "(SELECT count(*) FROM phone_call_recordings "
            " WHERE size_bytes > :old_limit)"
        ),
        {"old_limit": _OLD_LIMIT},
    )
    if oversized:
        raise RuntimeError("audio_closeout_downgrade_blocked_by_oversized_rows")

    for table in ("phone_call_recordings", "telegram_voice_archives"):
        op.drop_column(table, "summarized_at")
        op.drop_column(table, "summary_reason_code")
        op.drop_column(table, "summary_model")
        op.drop_column(table, "summary_text")
        op.drop_column(table, "summary_status")
    op.drop_column(
        "telegram_voice_archives",
        "transcription_recovery_authorized_at",
    )
    op.drop_column("dropbox_call_inbox_states", "backfill_version")

    op.drop_constraint(
        "ck_phone_call_recordings_bounded_size",
        "phone_call_recordings",
        type_="check",
    )
    op.create_check_constraint(
        "ck_phone_call_recordings_bounded_size",
        "phone_call_recordings",
        f"size_bytes > 0 AND size_bytes <= {_OLD_LIMIT}",
    )
    op.drop_constraint(
        "ck_telegram_voice_archives_size",
        "telegram_voice_archives",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_voice_archives_size",
        "telegram_voice_archives",
        f"size_bytes > 0 AND size_bytes <= {_OLD_LIMIT}",
    )
