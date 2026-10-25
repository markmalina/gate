"""Protected discovery/download metadata, including explicit per-file failures."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.watch_voice_inventory import (
    _object,
    decode_cursor,
    encode_cursor,
    iso,
)
from app.models.watch_voice import WatchVoiceSource

_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_I = {"type": "integer", "minimum": 0}
SOURCE_INVENTORY_SCHEMA = _object({
    "schema": {"const": "watch-voice-sources.v1"}, "observed_at": _S,
    "items": {"type": "array", "maxItems": 50, "items": _object({
        **{k: _S for k in ("event_id", "filename", "source_file_id", "revision", "state")},
        "size_bytes": {"type": ["integer", "null"], "minimum": 0},
        **{k: _NS for k in ("dropbox_modified_at", "detected_at", "last_error",
            "next_attempt_at", "lease_expires_at", "archived_at", "full_text_selector")},
        "download_attempt_count": _I,
    })},
    "count": _I, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    "has_more": {"type": "boolean"}, "next_cursor": _NS,
    "private_metadata": {"const": True}, "audio_included": {"const": False},
    "provider_calls": {"const": 0}, "mutations": {"const": 0},
})


async def source_inventory(
    session: AsyncSession, settings: Settings, *, limit: int = 20, cursor: str | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("watch_voice_inventory_limit_invalid")
    after = decode_cursor(cursor)
    query = select(WatchVoiceSource).where(
        WatchVoiceSource.workspace_key == settings.gate_workspace_id,
        WatchVoiceSource.account_id == settings.dropbox_call_account_id,
    )
    if after:
        time, key = after
        query = query.where(or_(WatchVoiceSource.detected_at > time, and_(
            WatchVoiceSource.detected_at == time, WatchVoiceSource.id > key)))
    rows = list((await session.scalars(query.order_by(
        WatchVoiceSource.detected_at, WatchVoiceSource.id).limit(limit + 1))).all())
    more, rows = len(rows) > limit, rows[:limit]
    items = [{
        "event_id": r.id, "filename": r.filename, "source_file_id": r.file_id,
        "revision": r.revision, "size_bytes": r.size_bytes, "state": r.state,
        "dropbox_modified_at": iso(r.dropbox_modified_at), "detected_at": iso(r.detected_at),
        "download_attempt_count": r.attempt_count, "last_error": r.last_error,
        "next_attempt_at": iso(r.next_attempt_at), "lease_expires_at": iso(r.lease_expires_at),
        "archived_at": iso(r.archived_at),
        "full_text_selector": "watch-voice:" + r.id if r.state == "archived" else None,
    } for r in rows]
    return {
        "schema": "watch-voice-sources.v1", "observed_at": datetime.now(UTC).isoformat(),
        "items": items, "count": len(items), "limit": limit, "has_more": more,
        "next_cursor": encode_cursor(SimpleNamespace(
            received_at=rows[-1].detected_at, id=rows[-1].id)) if more else None,
        "private_metadata": True, "audio_included": False, "provider_calls": 0, "mutations": 0,
    }
