"""Read-only owner inventory. No audio, provider I/O, claims or inferred timestamps."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.watch_voice_github import transcript_parts
from app.models.billing import BillingBudgetReservationModel
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceNote

# Select columns explicitly: never hydrate the original audio blob for a listing.
FIELDS = (
    "id", "filename", "file_id", "revision", "size_bytes", "duration_seconds",
    "dropbox_modified_at", "received_at", "transcription_state", "transcript",
    "model", "transcribed_at", "delivery_state", "next_part", "issue_url",
    "github_received_at", "delivered_at", "last_error",
    "detected_at", "processing_started_at", "processing_finished_at", "processing_attempt_count",
    "provider_started_at", "provider_finished_at", "provider_request_count", "provider_elapsed_ms",
    "provider_finished_count",
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_STRING = {"type": "string"}
_NULL_STRING = {"type": ["string", "null"]}
_INT = {"type": "integer", "minimum": 0}
_NULL_INT = {"type": ["integer", "null"], "minimum": 0}
_BOOL = {"type": "boolean"}
INVENTORY_SCHEMA = _object({
    "schema": {"const": "watch-voice-inventory.v1"}, "observed_at": _STRING,
    "items": {"type": "array", "maxItems": 50, "items": _object({
        **{k: _STRING for k in ("event_id", "filename", "source_file_id", "revision",
                               "transcription_state", "delivery_state",
                               "unknown_fields_reason")},
        "size_bytes": _INT, "duration_seconds": _NULL_INT,
        "times": _object({k: _NULL_STRING for k in (
            "recording_finished_at", "detected_at", "queued_at", "dropbox_modified_at",
            "archived_at", "transcription_started_at", "transcription_finished_at",
            "first_publication_at", "full_publication_confirmed_at",
            "provider_started_at", "provider_finished_at",
        )}),
        "attempts": _object({"job_attempt_count": _NULL_INT,
            "provider_request_count": _NULL_INT, "budget_reservation_count": _INT,
            "provider_finished_count": _NULL_INT, "provider_timing_complete": _BOOL,
            "basis": _STRING}),
        "last_error": _NULL_STRING, "waiting_reason": _NULL_STRING,
        "publication": _object({"issue_url": _NULL_STRING, "expected_parts": _NULL_INT,
            "confirmed_parts": _INT, "journal_complete": _BOOL,
            "external_content_verified": {"const": False}, "full_text_selector": _STRING}),
        "cost": _object({"kind": {"enum": ["provider_reported", "unknown"]},
            "microusd": _NULL_INT, "settled_component_microusd": _INT,
            "unresolved_reservations": _INT, "basis": _STRING}),
    })},
    "count": _INT, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    "has_more": _BOOL, "next_cursor": _NULL_STRING, "ordering": _STRING,
    "consistency": _STRING, "private_metadata": {"const": True},
    "transcript_included": {"const": False}, "audio_included": {"const": False},
    "provider_calls": {"const": 0}, "mutations": {"const": 0},
})


def utc(value: datetime) -> datetime:
    # SQLite fixtures drop tzinfo. Production PostgreSQL columns are timestamptz.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    return utc(value).isoformat() if value is not None else None


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        if not value or len(value) > 512:
            raise ValueError
        obj = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(obj, list) or len(obj) != 3 or obj[0] != 1:
            raise ValueError
        time = datetime.fromisoformat(obj[1])
        if time.tzinfo is None:
            raise ValueError
        return time.astimezone(UTC), str(UUID(obj[2]))
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise ValueError("watch_voice_inventory_cursor_invalid") from None


def encode_cursor(row: Any) -> str:
    raw = json.dumps([1, iso(row.received_at), row.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def waiting_reason(row: Any, *, complete: bool) -> str | None:
    if complete:
        return None
    if row.transcription_state == "unknown" or row.delivery_state == "unknown":
        return "reconciliation_required_no_automatic_retry"
    if row.transcription_state == "rejected":
        return row.last_error or "transcription_rejected"
    if row.delivery_state == "blocked":
        return row.last_error or "publication_blocked"
    if row.transcription_state == "processing" or row.delivery_state == "sending":
        return "effect_in_progress"
    if row.transcription_state == "transcribed":
        return "awaiting_remaining_publication_parts"
    return "awaiting_driver"


def card(row: Any, reservations: list[Any]) -> dict[str, Any]:
    parts = len(transcript_parts(row.transcript)) if row.transcript else None
    complete = bool(
        row.transcription_state == "transcribed" and row.delivery_state == "delivered"
        and row.issue_url and parts is not None and row.next_part == parts
        and row.delivered_at is not None
    )
    settled = sum(r.settled_microusd or 0 for r in reservations if r.state == "settled")
    unresolved = any(r.state not in ("settled", "released") for r in reservations)
    cost_known = bool(reservations) and not unresolved
    return {
        "event_id": row.id, "filename": row.filename,
        "source_file_id": row.file_id, "revision": row.revision,
        "size_bytes": row.size_bytes, "duration_seconds": row.duration_seconds,
        "transcription_state": row.transcription_state, "delivery_state": row.delivery_state,
        "times": {
            "recording_finished_at": None, "detected_at": iso(getattr(row, "detected_at", None)),
            "queued_at": iso(row.received_at) if getattr(row, "detected_at", None) else None,
            "dropbox_modified_at": iso(row.dropbox_modified_at),
            "archived_at": iso(row.received_at),
            "transcription_started_at": iso(getattr(row, "processing_started_at", None)),
            "transcription_finished_at": iso(
                getattr(row, "processing_finished_at", None) or row.transcribed_at),
            "provider_started_at": iso(getattr(row, "provider_started_at", None)),
            "provider_finished_at": iso(getattr(row, "provider_finished_at", None)),
            "first_publication_at": iso(row.github_received_at),
            "full_publication_confirmed_at": iso(row.delivered_at),
        },
        "attempts": {
            "job_attempt_count": getattr(row, "processing_attempt_count", None),
            "provider_request_count": getattr(row, "provider_request_count", None),
            "provider_finished_count": getattr(row, "provider_finished_count", None),
            "provider_timing_complete": (getattr(row, "provider_request_count", None) is not None
                and getattr(row, "provider_request_count", None)
                == getattr(row, "provider_finished_count", None)),
            "budget_reservation_count": len(reservations),
            "basis": "durable_observed_counters_legacy_unknown_reservations_separate",
        },
        "last_error": row.last_error,
        "waiting_reason": waiting_reason(row, complete=complete),
        "publication": {
            "issue_url": row.issue_url, "expected_parts": parts,
            "confirmed_parts": row.next_part, "journal_complete": complete,
            "external_content_verified": False,
            "full_text_selector": "watch-voice:" + row.id,
        },
        "cost": {
            "kind": "provider_reported" if cost_known else "unknown",
            "microusd": settled if cost_known else None,
            "settled_component_microusd": settled,
            "unresolved_reservations": sum(r.state not in ("settled", "released")
                                           for r in reservations),
            "basis": "exact_normal_and_authorized_recovery_reservations",
        },
        "unknown_fields_reason": "device_stop_unobserved_legacy_missing_values_not_inferred",
    }


async def protected_inventory(
    session: AsyncSession, settings: Settings, *, limit: int = 20, cursor: str | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("watch_voice_inventory_limit_invalid")
    after = decode_cursor(cursor)
    query = select(*(getattr(WatchVoiceNote, name) for name in FIELDS)).where(
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    )
    if after is not None:
        time, note_id = after
        query = query.where(or_(
            WatchVoiceNote.received_at > time,
            and_(WatchVoiceNote.received_at == time, WatchVoiceNote.id > note_id),
        ))
    rows = list((await session.execute(query.order_by(
        WatchVoiceNote.received_at, WatchVoiceNote.id,
    ).limit(limit + 1))).all())
    more = len(rows) > limit
    rows = rows[:limit]
    keys = {f"watch-voice:{r.id}{suffix}" for r in rows
            for suffix in ("", ":recovery1", ":postpayment1")}
    reservations = list((await session.scalars(
        select(BillingBudgetReservationModel)
        .join(Workspace, Workspace.id == BillingBudgetReservationModel.workspace_id)
        .where(Workspace.key == settings.gate_workspace_id,
               BillingBudgetReservationModel.reservation_key.in_(keys))
    )).all()) if keys else []
    items = [card(r, [b for b in reservations if b.reservation_key in {
        f"watch-voice:{r.id}", f"watch-voice:{r.id}:recovery1",
        f"watch-voice:{r.id}:postpayment1",
    }]) for r in rows]
    return {
        "schema": "watch-voice-inventory.v1", "observed_at": datetime.now(UTC).isoformat(),
        "items": items, "count": len(items), "limit": limit, "has_more": more,
        "next_cursor": encode_cursor(rows[-1]) if more else None,
        "ordering": "archived_at_then_event_id_ascending",
        "consistency": "live_keyset_not_a_frozen_snapshot",
        "private_metadata": True, "transcript_included": False, "audio_included": False,
        "provider_calls": 0, "mutations": 0,
    }
