"""Protected current journal status, including sources not yet archived.

One SELECT observes both journals. This is not an external Mark readback, an
audio integrity check, a Work result acceptance, or permission to retry STT.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.watch_voice_github import WatchVoiceError, transcript_parts
from app.infrastructure.watch_voice_inventory import _object, iso
from app.infrastructure.watch_voice_journal import safe_code
from app.models.watch_voice import (
    WatchVoiceNote,
    WatchVoiceSource,
    WatchVoiceWorkClaim,
    WatchVoiceWorkResult,
)

SOURCE_FIELDS = (
    "id", "file_id", "revision", "filename", "size_bytes", "state", "last_error",
    "detected_at", "archived_at", "dropbox_modified_at", "next_attempt_at",
    "card_state", "card_issue_number", "card_issue_url", "card_received_at", "card_error",
)
NOTE_FIELDS = (
    "id", "file_id", "revision", "filename", "size_bytes", "sha256", "received_at",
    "dropbox_modified_at",
    "transcript_origin",
    "transcription_state", "transcript", "model", "transcribed_at", "delivery_state",
    "next_part", "issue_number", "issue_url", "delivered_at", "last_error",
    "processing_started_at", "processing_finished_at", "provider_request_count",
    "provider_finished_count", "github_received_at",
)

_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_NI = {"type": ["integer", "null"], "minimum": 0}
_B = {"type": "boolean"}
STATUS_SCHEMA = _object({
    "schema": {"const": "watch-voice-status.v2"}, "observed_at": _S,
    "event_id": _S, "source_file_id": _S, "revision": _S, "filename": _S,
    "size_bytes": _NI, "note_last_error": _NS,
    "source": _object({"state": _S, "last_error": _NS, "next_attempt_at": _NS}),
    "card": _object({"state": _S, "issue_number": _NI, "issue_url": _NS,
                     "last_error": _NS}),
    "original": _object({"state": _S, "selector": _NS, "audio_sha256": _NS,
                         "archive_size_matches": _B, "integrity_verified": {"const": False}}),
    "transcription": _object({
        "state": _S, "model": _NS, "origin": _NS, "time_basis": _S,
        "provider_request_count": _NI, "provider_finished_count": _NI,
        "characters": _NI, "utf8_bytes": _NI, "sha256": _NS,
        "full_text_selector": _NS,
    }),
    "publication": _object({
        "state": _S, "issue_number": _NI, "issue_url": _NS,
        "expected_parts": _NI, "confirmed_parts": {"type": "integer", "minimum": 0},
        "journal_complete": _B, "external_content_verified": {"const": False},
    }),
    "times": _object({k: _NS for k in (
        "recording_started_at", "dropbox_modified_at", "detected_at", "archived_at",
        "card_received_at", "transcription_started_at", "transcription_finished_at",
        "transcribed_at", "first_text_received_at", "full_text_delivered_at",
    )}),
    "coordination": _object({
        "work_claim_submit": {"const": "internal_only_not_exposed"},
        "result_state": _S, "epoch": _NI, "last_error": _NS,
        "claim_expires_at": _NS, "started_at": _NS, "prepared_at": _NS, "saved_at": _NS,
        "accepted_result": {"oneOf": [{"type": "null"}, _object({
            "origin": {"const": "work"}, "sha256": _S, "accepted_at": _S,
        })]},
        "instruction_execution": {"const": "not_observed"},
        "retry_authorized": {"const": False},
    }),
    "consistency": {"const": "single_statement_journal_observation"},
    "private_metadata": {"const": True}, "transcript_included": {"const": False},
    "audio_included": {"const": False}, "provider_calls": {"const": 0},
    "mutations": {"const": 0},
})


def _error(code: str | None) -> str | None:
    return safe_code(code) if code is not None else None


async def protected_status(
    session: AsyncSession, settings: Settings, event_id: str,
) -> dict[str, Any] | None:
    """Exact UUID only; never choose another source when the archive is missing."""
    key = str(UUID(event_id))
    source = select(*(getattr(WatchVoiceSource, k) for k in SOURCE_FIELDS)).where(
        WatchVoiceSource.id == key,
        WatchVoiceSource.workspace_key == settings.gate_workspace_id,
        WatchVoiceSource.account_id == settings.dropbox_call_account_id,
    ).subquery("scoped_source")
    note = select(
        *(getattr(WatchVoiceNote, k) for k in NOTE_FIELDS),
        func.length(WatchVoiceNote.data).label("archive_bytes"),
    ).where(
        WatchVoiceNote.id == key,
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    ).subquery("scoped_note")
    journal = select(
        WatchVoiceWorkResult.note_id, WatchVoiceWorkResult.state, WatchVoiceWorkResult.epoch,
        WatchVoiceWorkResult.revision, WatchVoiceWorkResult.audio_sha256,
        WatchVoiceWorkResult.issue_number, WatchVoiceWorkResult.issue_url,
        WatchVoiceWorkResult.transcript_sha256, WatchVoiceWorkResult.accepted_at,
        WatchVoiceWorkResult.last_error, WatchVoiceWorkResult.prepared_at,
        WatchVoiceWorkResult.saved_at, WatchVoiceWorkResult.claim_id,
    ).where(WatchVoiceWorkResult.note_id == key).subquery("scoped_result")
    claim = select(WatchVoiceWorkClaim.id, WatchVoiceWorkClaim.expires_at,
                   WatchVoiceWorkClaim.started_at).where(
        WatchVoiceWorkClaim.note_id == key).subquery("scoped_claim")
    query = select(
        *(source.c[k].label("source_" + k) for k in SOURCE_FIELDS),
        *(note.c[k].label("note_" + k) for k in NOTE_FIELDS),
        note.c.archive_bytes,
        *(journal.c[k].label("result_" + k) for k in (
            "state", "epoch", "revision", "audio_sha256", "issue_number", "issue_url",
            "transcript_sha256", "accepted_at", "last_error",
            "prepared_at", "saved_at",
        )),
        claim.c.expires_at.label("claim_expires_at"),
        claim.c.started_at.label("claim_started_at"),
    ).select_from(source.join(note, source.c.id == note.c.id, full=True).outerjoin(
        journal, note.c.id == journal.c.note_id,
    ).outerjoin(claim, journal.c.claim_id == claim.c.id))
    # This session belongs to the caller. A read must neither flush their
    # pending writes nor take ownership by committing/rolling back them.
    with session.no_autoflush:
        row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        return None
    s = SimpleNamespace(**{k: row["source_" + k] for k in SOURCE_FIELDS}) \
        if row["source_id"] is not None else None
    n = SimpleNamespace(**{k: row["note_" + k] for k in NOTE_FIELDS}) \
        if row["note_id"] is not None else None
    if s is not None and n is not None:
        if (s.file_id, s.revision, s.size_bytes) != (n.file_id, n.revision, n.size_bytes):
            raise WatchVoiceError("watch_voice_status_source_conflict")
        if s.card_state == "saved" and n.issue_number is not None and (
            s.card_issue_number, s.card_issue_url
        ) != (n.issue_number, n.issue_url):
            raise WatchVoiceError("watch_voice_status_receipt_conflict")
    identity = s or n
    assert identity is not None
    text = n.transcript if n is not None else None
    encoded = text.encode("utf-8") if text else None
    parts = len(transcript_parts(text)) if text else None
    accepted: dict[str, Any] | None = None
    if row["result_state"] is not None:
        if n is None or (n.revision, n.sha256, n.issue_number, n.issue_url) != (
            row["result_revision"], row["result_audio_sha256"], row["result_issue_number"],
            row["result_issue_url"],
        ):
            raise WatchVoiceError("watch_voice_status_result_conflict")
        if row["result_transcript_sha256"] is not None and (
            n.transcript_origin != "work" or not encoded
            or hashlib.sha256(encoded).hexdigest() != row["result_transcript_sha256"]
        ):
            raise WatchVoiceError("watch_voice_status_result_conflict")
        if row["result_state"] == "accepted":
            if (not row["result_accepted_at"] or not row["result_transcript_sha256"]
                    or n.delivery_state != "delivered" or not n.delivered_at
                    or n.next_part != parts):
                raise WatchVoiceError("watch_voice_status_result_conflict")
            accepted = {"origin": "work", "sha256": row["result_transcript_sha256"],
                        "accepted_at": iso(row["result_accepted_at"])}
    complete = bool(n is not None and n.transcription_state == "transcribed"
                    and n.delivery_state == "delivered" and n.issue_number and n.issue_url
                    and n.delivered_at and parts is not None and n.next_part == parts)
    size_matches = bool(n is not None and row["archive_bytes"] == n.size_bytes)
    return {
        "schema": "watch-voice-status.v2", "observed_at": datetime.now(UTC).isoformat(),
        "event_id": key, "source_file_id": identity.file_id, "revision": identity.revision,
        "filename": identity.filename, "size_bytes": identity.size_bytes,
        # The legacy Note uses one error field for STT and text delivery.
        # Do not attribute a shared journal error to an unproven phase.
        "note_last_error": _error(n.last_error) if n else None,
        "source": {
            "state": s.state if s else "legacy_archive",
            "last_error": _error(s.last_error) if s else None,
            "next_attempt_at": iso(s.next_attempt_at) if s else None,
        },
        "card": {
            "state": s.card_state if s else "legacy",
            "issue_number": s.card_issue_number if s else None,
            "issue_url": s.card_issue_url if s else None,
            "last_error": _error(s.card_error) if s else None,
        },
        "original": {
            "state": "archived_unverified" if size_matches else (
                "archive_size_conflict" if n else "not_archived"),
            "selector": "watch-voice-file:" + key if size_matches else None,
            "audio_sha256": n.sha256 if n else None,
            "archive_size_matches": size_matches, "integrity_verified": False,
        },
        "transcription": {
            "state": n.transcription_state if n else "not_started",
            "model": n.model if n else None,
            "origin": n.transcript_origin if n and text else None,
            "time_basis": ("gate_received_work_text" if n.transcript_origin == "work"
                           else "gate_stt_finished") if n and text else "unobserved",
            "provider_request_count": n.provider_request_count if n else None,
            "provider_finished_count": n.provider_finished_count if n else None,
            "characters": len(text) if text else None,
            "utf8_bytes": len(encoded) if encoded else None,
            "sha256": hashlib.sha256(encoded).hexdigest() if encoded else None,
            "full_text_selector": "watch-voice:" + key if text else None,
        },
        "publication": {
            "state": n.delivery_state if n else "not_started",
            "issue_number": n.issue_number if n else None,
            "issue_url": n.issue_url if n else None,
            "expected_parts": parts, "confirmed_parts": n.next_part if n else 0,
            "journal_complete": complete, "external_content_verified": False,
        },
        "times": {
            "recording_started_at": None,
            "dropbox_modified_at": iso(identity.dropbox_modified_at),
            "detected_at": iso(s.detected_at) if s else None,
            "archived_at": iso(n.received_at) if n else None,
            "card_received_at": iso(s.card_received_at) if s else None,
            "transcription_started_at": iso(n.processing_started_at) if n else None,
            "transcription_finished_at": iso(n.processing_finished_at) if n else None,
            "transcribed_at": iso(n.transcribed_at) if n else None,
            "first_text_received_at": iso(n.github_received_at) if n else None,
            "full_text_delivered_at": iso(n.delivered_at) if n else None,
        },
        "coordination": {
            "work_claim_submit": "internal_only_not_exposed", "accepted_result": accepted,
            "result_state": row["result_state"] or "not_admitted",
            "epoch": row["result_epoch"], "last_error": _error(row["result_last_error"]),
            "claim_expires_at": iso(row["claim_expires_at"]),
            "started_at": iso(row["claim_started_at"]),
            "prepared_at": iso(row["result_prepared_at"]),
            "saved_at": iso(row["result_saved_at"]),
            "instruction_execution": "not_observed", "retry_authorized": False,
        },
        "consistency": "single_statement_journal_observation", "private_metadata": True,
        "transcript_included": False, "audio_included": False, "provider_calls": 0,
        "mutations": 0,
    }
