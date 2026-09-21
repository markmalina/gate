"""One final exact classification after fixing valid empty transcription responses."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0107_no_speech_recovery"
down_revision: str | None = "0106_owner_voice_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VOICE_SOURCES = (
    ("a4901376-2dee-49b3-bc7e-011716842c2f", 7110),
    ("fe5dad7b-37a1-400e-af5d-e202aa672f0e", 7697),
    ("e5d1d565-7ba3-48e2-9c44-40cd8e34207b", 566136),
    ("5be96bc4-6fbf-40ff-a758-315dfa1cd25d", 53196),
)
_OLD_STATUSES = (
    "received", "disabled", "rejected", "transcribed", "unknown", "processing",
    "recovery_v8", "recovery_v9", "recovery_v10", "recovery_v11",
    "recovery_v12", "recovery_v13", "recovery_v14",
)
_VOICE_SCOPE = """
id = CAST(:id AS uuid)
AND account_id = '0d8e025f-323d-5e86-a44c-a620f690ae4f'::uuid
AND project_id = 'e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0'::uuid
AND transcription_status IN ('unknown', 'rejected')
AND transcription_reason_code IN (
  'openai_transcription_response_unknown', 'billing_reservation_replayed')
AND transcript_text IS NULL AND summary_text IS NULL
AND size_bytes = :size AND octet_length(data) = :size
AND duration_seconds > 0 AND length(sha256) = 64
"""


def _constraint(statuses: tuple[str, ...]) -> None:
    name = "ck_phone_call_recordings_transcription_status"
    op.drop_constraint(name, "phone_call_recordings", type_="check")
    values = ", ".join(f"'{status}'" for status in statuses)
    op.create_check_constraint(name, "phone_call_recordings", f"transcription_status IN ({values})")


def upgrade() -> None:
    _constraint((*_OLD_STATUSES, "recovery_v15"))
    for archive_id, size in _VOICE_SOURCES:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = 4 "
            f"WHERE {_VOICE_SCOPE} AND transcription_recovery_generation IS NULL"
        ), {"id": archive_id, "size": size})
    op.get_bind().execute(sa.text("""
        UPDATE phone_call_recordings SET transcription_status = 'recovery_v15'
        WHERE id = '11a28248-e86f-4f57-af97-d9160187a93e'::uuid
          AND workspace_key = 'mark-personal' AND size_bytes = 515801
          AND octet_length(data) = size_bytes AND duration_seconds > 0
          AND transcription_status = 'unknown'
          AND transcription_reason_code = 'openai_transcription_response_unknown'
          AND transcript_text IS NULL AND summary_text IS NULL
    """))


def downgrade() -> None:
    for archive_id, size in _VOICE_SOURCES:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = NULL "
            f"WHERE {_VOICE_SCOPE} AND transcription_recovery_generation = 4"
        ), {"id": archive_id, "size": size})
    op.get_bind().execute(sa.text("""
        UPDATE phone_call_recordings SET transcription_status = 'unknown'
        WHERE id = '11a28248-e86f-4f57-af97-d9160187a93e'::uuid
          AND workspace_key = 'mark-personal'
          AND transcription_status = 'recovery_v15'
    """))
    _constraint(_OLD_STATUSES)
