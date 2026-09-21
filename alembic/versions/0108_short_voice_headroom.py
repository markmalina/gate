"""One exact final voice grant with minimum request-cost headroom."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0108_short_voice_headroom"
down_revision: str | None = "0107_no_speech_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VOICE_SOURCES = (("a4901376-2dee-49b3-bc7e-011716842c2f", 7110),)
_SCOPE = """
id = CAST(:id AS uuid)
AND account_id = '0d8e025f-323d-5e86-a44c-a620f690ae4f'::uuid
AND project_id = 'e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0'::uuid
AND transcription_status IN ('unknown', 'rejected')
AND transcription_reason_code IN (
  'openai_transcription_response_unknown', 'billing_reservation_replayed',
  'billing_settlement_exceeds_reservation')
AND transcript_text IS NULL AND summary_text IS NULL
AND size_bytes = :size AND octet_length(data) = :size
AND duration_seconds = 1 AND length(sha256) = 64
"""


def upgrade() -> None:
    for archive_id, size in _VOICE_SOURCES:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = 5 "
            f"WHERE {_SCOPE} AND transcription_recovery_generation IS NULL"
        ), {"id": archive_id, "size": size})


def downgrade() -> None:
    for archive_id, size in _VOICE_SOURCES:
        op.get_bind().execute(sa.text(
            "UPDATE telegram_voice_archives SET transcription_recovery_generation = NULL "
            f"WHERE {_SCOPE} AND transcription_recovery_generation = 5"
        ), {"id": archive_id, "size": size})
