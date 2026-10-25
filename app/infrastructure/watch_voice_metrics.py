"""Redacted Voice measurements for the existing Gate status surface.

Process observations are explicitly local, reset on restart, and never presented
as cluster-wide or durable liveness. Queue, progress and charges come from SQL.
"""
from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.watch_voice_inventory import _object as schema_object
from app.infrastructure.watch_voice_inventory import iso, utc
from app.models.billing import BillingBudgetReservationModel as Reservation
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceNote as Note


@dataclass
class CycleObservation:
    started_at: datetime | None = None
    completed_at: datetime | None = None
    scanned_at: datetime | None = None
    error_code: str | None = None


OBSERVATION = CycleObservation()

_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_I = {"type": "integer", "minimum": 0}
_N = {"type": ["number", "null"], "minimum": 0}
_B = {"type": "boolean"}
_DISTRIBUTION = schema_object({"n": _I, "p50_seconds": _N, "p95_seconds": _N,
    "max_seconds": _N, "preliminary": _B, "method": {"const": "nearest_rank"}})
METRICS_SCHEMA = schema_object({
    "schema": {"const": "watch-voice-metrics.v1"}, "observed_at": _S,
    "expected_driver": {"enum": ["actions", "persistent", "disabled", "conflict"]},
    "running_driver_proven": {"const": False}, "cycle_observation_scope": _S,
    **{key: _NS for key in ("last_cycle_started_at", "last_cycle_completed_at",
        "last_successful_scan_at", "last_cycle_error", "last_useful_progress_at")},
    "queue_size": _I, "oldest_waiting_age_seconds": _N, "unknown_count": _I,
    "latency": schema_object({"period_start": _S, "period_end": _S,
        "sample_cap": _I, "truncated": _B, "basis": _S,
        "phases": schema_object({name: _DISTRIBUTION for name in (
            "dropbox_to_archive", "archive_to_transcription_finished",
            "dropbox_to_full_publication", "dropbox_to_transcription_start", "provider_request",
        )})}),
    "voice_cost": schema_object({"period": _S, "settled_microusd": _I,
        "reserved_or_unknown_microusd": _I, "reservation_count": _I,
        "infrastructure_cost": _S}),
    "signal_codes": {"type": "array", "items": _S},
    "notification_delivery": _S, "private_content_exposed": {"const": False},
})


def observed_cycle[**P, R](
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    @wraps(function)
    async def observed(*args: P.args, **kwargs: P.kwargs) -> R:
        OBSERVATION.started_at = datetime.now(UTC)
        try:
            result = await function(*args, **kwargs)
        except Exception as exc:
            code = getattr(exc, "code", "watch_voice_cycle_failed")
            OBSERVATION.error_code = (code if isinstance(code, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) else "watch_voice_cycle_failed")
            raise
        else:
            OBSERVATION.completed_at = datetime.now(UTC)
            OBSERVATION.error_code = None
            return result
    return observed


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(x for x in values if math.isfinite(x) and x >= 0)
    def percentile(fraction: float) -> float | None:
        return ordered[math.ceil(len(ordered) * fraction) - 1] if ordered else None
    return {"n": len(ordered), "p50_seconds": percentile(.5),
            "p95_seconds": percentile(.95), "max_seconds": ordered[-1] if ordered else None,
            "preliminary": len(ordered) < 30, "method": "nearest_rank"}


def signals(*, expected: str, queue: int, oldest_age: float | None,
            progress_age: float | None, cycle_age: float | None,
            scan_age: float | None, unknown: int) -> list[str]:
    reasons = []
    if expected == "persistent" and queue:
        if cycle_age is not None and cycle_age > 120:
            reasons.append("watch_voice_cycle_stale")
        if (oldest_age is not None and oldest_age > 300
                and (progress_age is None or progress_age > 300)):
            reasons.append("watch_voice_queue_no_progress")
    if expected == "persistent" and scan_age is not None and scan_age > 180:
        reasons.append("watch_voice_scan_stale")
    if unknown:
        reasons.append("watch_voice_reconciliation_required")
    return reasons


async def snapshot(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    scope = (Note.workspace_key == settings.gate_workspace_id,
             Note.account_id == settings.dropbox_call_account_id)
    counts = list((await session.execute(select(
        Note.transcription_state, Note.delivery_state, func.count(), func.min(Note.received_at),
    ).where(*scope).group_by(Note.transcription_state, Note.delivery_state))).all())
    pending = [r for r in counts if r[1] != "delivered"]
    queue = sum(r[2] for r in pending)
    oldest = min((utc(r[3]) for r in pending), default=None)
    unknown = sum(r[2] for r in counts if r[0] == "unknown" or r[1] == "unknown")
    progress = await session.scalar(select(func.max(Note.delivered_at)).where(*scope))
    rows = list((await session.execute(select(
        Note.dropbox_modified_at, Note.received_at, Note.transcribed_at, Note.delivered_at,
    ).where(*scope, Note.transcription_state == "transcribed", Note.delivery_state == "delivered",
            Note.delivered_at >= start).order_by(Note.delivered_at.desc()).limit(1001))).all())
    truncated = len(rows) > 1000
    rows = rows[:1000]
    phases = {}
    for name, before, after in (("dropbox_to_archive", 0, 1),
                                ("archive_to_transcription_finished", 1, 2),
                                ("dropbox_to_full_publication", 0, 3)):
        values = []
        for row in rows:
            left, right = row[before], row[after]
            if left is not None and right is not None:
                values.append((utc(right) - utc(left)).total_seconds())
        phases[name] = distribution(values)
    phases["dropbox_to_transcription_start"] = distribution([])
    phases["provider_request"] = distribution([])
    def age(value: datetime | None) -> float | None:
        return max(0., (now - utc(value)).total_seconds()) if value else None
    expected = ("conflict" if settings.gate_watch_voice_enabled
                and settings.gate_watch_voice_actions_enabled else "persistent"
                if settings.gate_watch_voice_enabled else "actions"
                if settings.gate_watch_voice_actions_enabled else "disabled")
    charges = list((await session.execute(select(
        Reservation.state, Reservation.settled_microusd, Reservation.reserved_microusd,
    ).join(Workspace, Workspace.id == Reservation.workspace_id)
      .join(Note, or_(*[Reservation.reservation_key == "watch-voice:" + Note.id + suffix
                       for suffix in ("", ":recovery1", ":postpayment1")]))
      .where(*scope, Workspace.key == settings.gate_workspace_id,
             Reservation.period_key == now.strftime("%Y-%m")))).all())
    reasons = signals(expected=expected, queue=queue, oldest_age=age(oldest),
        progress_age=age(progress), cycle_age=age(OBSERVATION.completed_at),
        scan_age=age(OBSERVATION.scanned_at), unknown=unknown)
    return {
        "schema": "watch-voice-metrics.v1", "observed_at": now.isoformat(),
        "expected_driver": expected, "running_driver_proven": False,
        "cycle_observation_scope": "current_api_process_only_not_durable",
        "last_cycle_started_at": iso(OBSERVATION.started_at),
        "last_cycle_completed_at": iso(OBSERVATION.completed_at),
        "last_successful_scan_at": iso(OBSERVATION.scanned_at),
        "last_cycle_error": OBSERVATION.error_code,
        "last_useful_progress_at": iso(progress), "queue_size": queue,
        "oldest_waiting_age_seconds": age(oldest), "unknown_count": unknown,
        "latency": {"period_start": start.isoformat(), "period_end": now.isoformat(),
            "sample_cap": 1000, "truncated": truncated, "phases": phases,
            "basis": "journal_completed_records_only_not_provider_time"},
        "voice_cost": {"period": now.strftime("%Y-%m"),
            "settled_microusd": sum(r[1] or 0 for r in charges if r[0] == "settled"),
            "reserved_or_unknown_microusd": sum(r[2] for r in charges
                                                 if r[0] in ("reserved", "unknown")),
            "reservation_count": len(charges),
            "infrastructure_cost": "unknown_not_attributed"},
        "signal_codes": reasons,
        "notification_delivery": "not_configured_use_existing_gate_monitoring",
        "private_content_exposed": False,
    }
