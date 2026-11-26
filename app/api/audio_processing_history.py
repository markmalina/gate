"""The common readback of audio milestones in owner-scoped Gate tools."""

import re
from datetime import UTC, datetime
from typing import Any

_TIME_FIELDS = ("received_at", "queued_at", "recognition_started_at", "text_saved_at", "stopped_at")
_DURATION_FIELDS = ("queue_wait_seconds", "recognition_seconds")
_STOP_REASON = re.compile(r"[a-z][a-z0-9_]{0,127}")
_STALE_CLAIM_REASONS = frozenset({
    "watch_voice_stale_transcription",
    "dropbox_call_interrupted_after_reservation",
    "telegram_voice_stale_transcription",
})

AUDIO_PROCESSING_HISTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{name: {"type": ["string", "null"], "format": "date-time"} for name in _TIME_FIELDS},
        **{name: {"type": ["number", "null"], "minimum": 0} for name in _DURATION_FIELDS},
        "stop_reason": {"type": ["string", "null"], "maxLength": 128},
    },
    "required": [*_TIME_FIELDS, *_DURATION_FIELDS, "stop_reason"],
    "additionalProperties": False,
}


def watch_processing_history(recording: Any) -> dict[str, Any]:
    state = recording.transcription_state
    return audio_processing_history(
        recording,
        legacy_saved_at_is_reliable=state == "transcribed" and bool(recording.transcript),
        legacy_stop_reason=recording.last_error if state in {"rejected", "unknown", "disabled"}
        else None,
    )


def call_processing_history(recording: Any) -> dict[str, Any]:
    status = recording.transcription_status
    reason = getattr(recording, "transcription_reason_code", None)
    return audio_processing_history(
        recording,
        legacy_saved_at_is_reliable=(
            status == "transcribed" and bool(recording.transcript_text)
            and reason != "audio_identical_source_reused"
        ),
        legacy_stop_reason=reason if status in {"rejected", "unknown", "disabled"} else None,
    )


def telegram_processing_history(recording: Any) -> dict[str, Any]:
    # The former worker wrote receipt time and the former MCP route wrote start time.
    return audio_processing_history(
        recording,
        legacy_stop_reason=getattr(recording, "transcription_reason_code", None)
        if recording.transcription_status in {"rejected", "unknown", "disabled"} else None,
    )


def audio_processing_history(
    recording: Any,
    *,
    legacy_saved_at_is_reliable: bool = False,
    legacy_stop_reason: str | None = None,
) -> dict[str, Any]:
    received = _utc(getattr(recording, "received_at", None))
    queued = _utc(getattr(recording, "transcription_queued_at", None))
    started = _utc(getattr(recording, "recognition_started_at", None))
    saved = _utc(getattr(recording, "text_saved_at", None))
    stopped = _utc(getattr(recording, "transcription_stopped_at", None))
    if saved is None and legacy_saved_at_is_reliable:
        legacy_saved = _utc(getattr(recording, "transcribed_at", None))
        if legacy_saved is not None and (received is None or legacy_saved >= received):
            saved = legacy_saved
    reason = getattr(recording, "transcription_stop_reason", None) or legacy_stop_reason
    safe_reason = reason if isinstance(reason, str) and _STOP_REASON.fullmatch(reason) else None
    recognition_end = saved or (stopped if safe_reason not in _STALE_CLAIM_REASONS else None)
    return {
        "received_at": received.isoformat() if received is not None else None,
        "queued_at": queued.isoformat() if queued is not None else None,
        "recognition_started_at": started.isoformat() if started is not None else None,
        "text_saved_at": saved.isoformat() if saved is not None else None,
        "stopped_at": stopped.isoformat() if stopped is not None else None,
        "stop_reason": safe_reason,
        "queue_wait_seconds": _duration(queued, started),
        "recognition_seconds": _duration(started, recognition_end),
    }


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _duration(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds(), 3)
