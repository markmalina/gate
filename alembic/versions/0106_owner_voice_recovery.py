"""Authorize one archive-only attempt for four owner-confirmed missing transcripts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0106_owner_voice_recovery"
down_revision: str | None = "0105_seekable_audio_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDS = (
    "48d7d278-25e6-457d-b97e-096cfd90ad01",
    "a4901376-2dee-49b3-bc7e-011716842c2f",
    "e5d1d565-7ba3-48e2-9c44-40cd8e34207b",
    "5be96bc4-6fbf-40ff-a758-315dfa1cd25d",
)
_SCOPE = """
id = CAST(:id AS uuid)
AND account_id = '0d8e025f-323d-5e86-a44c-a620f690ae4f'::uuid
AND project_id = 'e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0'::uuid
AND transcription_status IN ('unknown', 'rejected')
AND transcription_reason_code IN (
  'openai_transcription_transport_unknown',
  'openai_transcription_response_unknown', 'billing_reservation_replayed')
AND transcript_text IS NULL AND summary_text IS NULL
AND size_bytes > 0 AND octet_length(data) = size_bytes
AND duration_seconds > 0 AND length(sha256) = 64
"""


def upgrade() -> None:
    for archive_id in _IDS:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = 3 "
            f"WHERE {_SCOPE} AND transcription_recovery_generation IS NULL "
            "AND transcription_recovery_authorized_at IS NULL"
        ), {"id": archive_id})
    op.get_bind().execute(sa.text("""
        UPDATE phone_call_recordings SET transcription_status = 'recovery_v14'
        WHERE id = '11a28248-e86f-4f57-af97-d9160187a93e'::uuid
          AND workspace_key = 'mark-personal' AND size_bytes = 515801
          AND octet_length(data) = size_bytes AND duration_seconds > 0
          AND transcription_status = 'unknown'
          AND transcription_reason_code = 'openai_transcription_response_unknown'
          AND transcript_text IS NULL AND summary_text IS NULL
    """))


def downgrade() -> None:
    for archive_id in _IDS:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = NULL "
            f"WHERE {_SCOPE} AND transcription_recovery_generation = 3"
        ), {"id": archive_id})
    op.get_bind().execute(sa.text("""
        UPDATE phone_call_recordings SET transcription_status = 'unknown'
        WHERE id = '11a28248-e86f-4f57-af97-d9160187a93e'::uuid
          AND workspace_key = 'mark-personal'
          AND transcription_status = 'recovery_v14'
          AND transcript_text IS NULL
    """))
