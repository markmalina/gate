"""Authorize two exact definite failures after repairing seekable audio decoding.

Revision ID: 0105_seekable_audio_recovery
Revises: 0104_dropbox_call_source_limit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0105_seekable_audio_recovery"
down_revision: str | None = "0104_dropbox_call_source_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXACT_RECORDINGS = (
    ("a1a82247-434e-4f43-8974-35a972ba9dc2", 25_505_021),
    ("050409d7-4e44-4df6-896f-c5023ced70ae", 37_585_783),
)
_OLD_STATUSES = (
    "received", "disabled", "rejected", "transcribed", "unknown", "processing",
    "recovery_v8", "recovery_v9", "recovery_v10", "recovery_v11",
    "recovery_v12", "recovery_v13",
)
_STATUS = "recovery_v14"
_CONSTRAINT = "ck_phone_call_recordings_transcription_status"


def _replace_constraint(statuses: tuple[str, ...]) -> None:
    op.drop_constraint(_CONSTRAINT, "phone_call_recordings", type_="check")
    values = ", ".join(f"'{status}'" for status in statuses)
    op.create_check_constraint(
        _CONSTRAINT, "phone_call_recordings", f"transcription_status IN ({values})"
    )


def upgrade() -> None:
    _replace_constraint((*_OLD_STATUSES, _STATUS))
    for recording_id, expected_size in _EXACT_RECORDINGS:
        op.get_bind().execute(
            sa.text(
                "UPDATE phone_call_recordings SET transcription_status = :status "
                "WHERE id = CAST(:id AS uuid) AND workspace_key = 'mark-personal' "
                "AND transcription_status = 'rejected' "
                "AND transcription_reason_code = 'openai_transcription_rejected_http_400' "
                "AND transcript_text IS NULL AND summary_text IS NULL "
                "AND size_bytes = :size AND octet_length(data) = :size "
                "AND duration_seconds > 0 AND length(sha256) = 64"
            ),
            {"status": _STATUS, "id": recording_id, "size": expected_size},
        )


def downgrade() -> None:
    # Withdraw only an unconsumed grant. Never overwrite completed/unknown work.
    for recording_id, expected_size in _EXACT_RECORDINGS:
        op.get_bind().execute(
            sa.text(
                "UPDATE phone_call_recordings SET transcription_status = 'rejected' "
                "WHERE id = CAST(:id AS uuid) AND transcription_status = :status "
                "AND transcript_text IS NULL AND size_bytes = :size"
            ),
            {"status": _STATUS, "id": recording_id, "size": expected_size},
        )
    _replace_constraint(_OLD_STATUSES)
