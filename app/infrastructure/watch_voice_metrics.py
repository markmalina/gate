"""Redacted Voice measurements for the existing Gate status surface.

Cycle, source, queue, phase and charge observations come from durable SQL.
Recent cycle evidence has an explicit deadline; HTTP200 alone is never liveness.
"""

from __future__ import annotations

import asyncio
import inspect
import json
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
from app.infrastructure.watch_voice_journal import (
    CURRENT_CYCLE,
    finish_cycle,
    latest_cycle,
    start_cycle,
)
from app.models.billing import BillingBudgetReservationModel as Reservation
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceCycle, WatchVoiceIncident, WatchVoiceSource
from app.models.watch_voice import WatchVoiceNote as Note


@dataclass
class CycleObservation:
    started_at: datetime | None = None
    completed_at: datetime | None = None
    scanned_at: datetime | None = None
    error_code: str | None = None
    scan_error_code: str | None = None


OBSERVATION = CycleObservation()

_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_I = {"type": "integer", "minimum": 0}
_N = {"type": ["number", "null"], "minimum": 0}
_B = {"type": "boolean"}
_DISTRIBUTION = schema_object(
    {
        "n": _I,
        "p50_seconds": _N,
        "p95_seconds": _N,
        "max_seconds": _N,
        "preliminary": _B,
        "method": {"const": "nearest_rank"},
    }
)
METRICS_SCHEMA = schema_object(
    {
        "schema": {"const": "watch-voice-metrics.v1"},
        "observed_at": _S,
        "expected_driver": {"enum": ["actions", "persistent", "disabled", "conflict"]},
        "running_driver_proven": _B,
        "cycle_observation_scope": _S,
        "monitor_host": {"const": "existing_dropbox_calls_loop"},
        "monitor_host_enabled": _B,
        "observed_driver": {"enum": ["actions", "persistent", None]},
        "deployment_source_sha": _NS,
        "last_cycle_id": _NS,
        "cycle_phase": _NS,
        "cycle_outcome": _NS,
        "liveness_evidence_valid_until": _NS,
        "source_counts": {"type": "object", "additionalProperties": _I},
        "incident": {
            "anyOf": [
                {"type": "null"},
                schema_object(
                    {
                        "id": _S,
                        "opened_at": _S,
                        "observed_at": _S,
                        "recovered_at": _NS,
                        "active_codes": {"type": "array", "items": _S},
                        "log_attempt_count": _I,
                        "delivery_basis": _S,
                    }
                ),
            ]
        },
        **{
            key: _NS
            for key in (
                "last_cycle_started_at",
                "last_cycle_completed_at",
                "last_successful_scan_at",
                "last_cycle_error",
                "last_scan_error",
                "last_useful_progress_at",
            )
        },
        "queue_size": _I,
        "oldest_waiting_age_seconds": _N,
        "unknown_count": _I,
        "latency": schema_object(
            {
                "period_start": _S,
                "period_end": _S,
                "sample_cap": _I,
                "truncated": _B,
                "basis": _S,
                "phases": schema_object(
                    {
                        name: _DISTRIBUTION
                        for name in (
                            "dropbox_to_archive",
                            "archive_to_transcription_finished",
                            "dropbox_to_full_publication",
                            "dropbox_to_transcription_start",
                            "provider_request",
                        )
                    }
                ),
            }
        ),
        "voice_cost": schema_object(
            {
                "period": _S,
                "settled_microusd": _I,
                "reserved_or_unknown_microusd": _I,
                "reservation_count": _I,
                "infrastructure_cost": _S,
            }
        ),
        "signal_codes": {"type": "array", "items": _S},
        "notification_delivery": _S,
        "private_content_exposed": {"const": False},
    }
)


def observed_cycle[**P, R](
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    @wraps(function)
    async def observed(*args: P.args, **kwargs: P.kwargs) -> R:
        OBSERVATION.started_at = datetime.now(UTC)
        arguments = inspect.signature(function).bind(*args, **kwargs).arguments
        settings, factory = arguments.get("settings"), arguments.get("factory")
        token = None
        if (
            isinstance(settings, Settings)
            and factory is not None
            and settings.gate_watch_voice_data_gate_approved
            and settings.gate_workspace_id == settings.gate_watch_voice_workspace_key
            and bool(settings.gate_watch_voice_enabled)
            != bool(settings.gate_watch_voice_actions_enabled)
        ):
            cycle_id = await start_cycle(
                factory,
                settings,
                driver=("persistent" if settings.gate_watch_voice_enabled else "actions"),
            )
            token = CURRENT_CYCLE.set((factory, cycle_id))
        try:
            result = await function(*args, **kwargs)
        except asyncio.CancelledError:
            await finish_cycle("cancelled", "watch_voice_cycle_cancelled")
            raise
        except Exception as exc:
            code = getattr(exc, "code", "watch_voice_cycle_failed")
            OBSERVATION.error_code = (
                code
                if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
                else "watch_voice_cycle_failed"
            )
            await finish_cycle("failure", code)
            raise
        else:
            OBSERVATION.completed_at = datetime.now(UTC)
            OBSERVATION.error_code = None
            await finish_cycle("success")
            return result
        finally:
            if token is not None:
                CURRENT_CYCLE.reset(token)

    return observed


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(x for x in values if math.isfinite(x) and x >= 0)

    def percentile(fraction: float) -> float | None:
        return ordered[math.ceil(len(ordered) * fraction) - 1] if ordered else None

    return {
        "n": len(ordered),
        "p50_seconds": percentile(0.5),
        "p95_seconds": percentile(0.95),
        "max_seconds": ordered[-1] if ordered else None,
        "preliminary": len(ordered) < 30,
        "method": "nearest_rank",
    }


def signals(
    *,
    expected: str,
    queue: int,
    oldest_age: float | None,
    progress_age: float | None,
    cycle_age: float | None,
    scan_age: float | None,
    unknown: int,
) -> list[str]:
    reasons = []
    if expected == "conflict":
        reasons.append("watch_voice_driver_conflict")
    if expected == "persistent" and queue:
        if cycle_age is not None and cycle_age > 120:
            reasons.append("watch_voice_cycle_stale")
        if (
            oldest_age is not None
            and oldest_age > 300
            and (progress_age is None or progress_age > 300)
        ):
            reasons.append("watch_voice_queue_no_progress")
    if expected == "persistent" and scan_age is not None and scan_age > 180:
        reasons.append("watch_voice_scan_stale")
    if unknown:
        reasons.append("watch_voice_reconciliation_required")
    return reasons


async def snapshot(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    scope = (
        Note.workspace_key == settings.gate_workspace_id,
        Note.account_id == settings.dropbox_call_account_id,
    )
    counts = list(
        (
            await session.execute(
                select(
                    Note.transcription_state,
                    Note.delivery_state,
                    func.count(),
                    func.min(Note.received_at),
                )
                .where(*scope)
                .group_by(Note.transcription_state, Note.delivery_state)
            )
        ).all()
    )
    pending = [r for r in counts if r[1] != "delivered"]
    queue = sum(r[2] for r in pending)
    oldest = min((utc(r[3]) for r in pending), default=None)
    unknown = sum(r[2] for r in counts if r[0] == "unknown" or r[1] == "unknown")
    progress = await session.scalar(select(func.max(Note.delivered_at)).where(*scope))
    rows = list(
        (
            await session.execute(
                select(
                    Note.dropbox_modified_at,
                    Note.received_at,
                    Note.transcribed_at,
                    Note.delivered_at,
                    Note.processing_started_at,
                    Note.provider_elapsed_ms,
                    Note.provider_request_count,
                    Note.provider_finished_at,
                    Note.provider_finished_count,
                )
                .where(
                    *scope,
                    Note.transcription_state == "transcribed",
                    Note.delivery_state == "delivered",
                    Note.delivered_at >= start,
                )
                .order_by(Note.delivered_at.desc())
                .limit(1001)
            )
        ).all()
    )
    truncated = len(rows) > 1000
    rows = rows[:1000]
    phases = {}
    for name, before, after in (
        ("dropbox_to_archive", 0, 1),
        ("archive_to_transcription_finished", 1, 2),
        ("dropbox_to_full_publication", 0, 3),
        ("dropbox_to_transcription_start", 0, 4),
    ):
        values = []
        for row in rows:
            left, right = row[before], row[after]
            if isinstance(left, datetime) and isinstance(right, datetime):
                values.append((utc(right) - utc(left)).total_seconds())
        phases[name] = distribution(values)
    phases["provider_request"] = distribution(
        [
            r[5] / 1000
            for r in rows
            if r[5] is not None and r[6] and r[7] is not None and r[6] == r[8]
        ]
    )

    def age(value: datetime | None) -> float | None:
        return max(0.0, (now - utc(value)).total_seconds()) if value else None

    expected = (
        "conflict"
        if settings.gate_watch_voice_enabled and settings.gate_watch_voice_actions_enabled
        else "persistent"
        if settings.gate_watch_voice_enabled
        else "actions"
        if settings.gate_watch_voice_actions_enabled
        else "disabled"
    )
    charges = list(
        (
            await session.execute(
                select(
                    Reservation.state,
                    Reservation.settled_microusd,
                    Reservation.reserved_microusd,
                )
                .join(Workspace, Workspace.id == Reservation.workspace_id)
                .join(
                    Note,
                    or_(
                        *[
                            Reservation.reservation_key == "watch-voice:" + Note.id + suffix
                            for suffix in ("", ":recovery1", ":postpayment1")
                        ]
                    ),
                )
                .where(
                    *scope,
                    Workspace.key == settings.gate_workspace_id,
                    Reservation.period_key == now.strftime("%Y-%m"),
                )
            )
        ).all()
    )
    cycle = await latest_cycle(session, settings)
    scans = await session.scalar(
        select(func.max(WatchVoiceCycle.scan_finished_at)).where(
            WatchVoiceCycle.workspace_key == settings.gate_workspace_id,
            WatchVoiceCycle.account_id == settings.dropbox_call_account_id,
        )
    )
    source_rows = await session.execute(
        select(WatchVoiceSource.state, func.count())
        .where(
            WatchVoiceSource.workspace_key == settings.gate_workspace_id,
            WatchVoiceSource.account_id == settings.dropbox_call_account_id,
        )
        .group_by(WatchVoiceSource.state)
    )
    source_counts = {str(state): count for state, count in source_rows}
    active = bool(cycle and cycle.outcome == "running" and utc(cycle.deadline_at) > now)
    cycle_age = age(cycle.heartbeat_at) if cycle else None
    if active:
        cycle_age = 0.0  # a bounded active phase is not an idle heartbeat gap
    reasons = signals(
        expected=expected,
        queue=queue,
        oldest_age=age(oldest),
        progress_age=0.0 if active else age(progress),
        cycle_age=cycle_age,
        scan_age=0.0
        if active
        and cycle
        and cycle.phase
        in {
            "dropbox_scan",
            "dropbox_delta",
            "source_download",
        }
        else age(scans),
        unknown=unknown,
    )
    if cycle and cycle.scan_error:
        reasons.append("watch_voice_scan_failed")
    if cycle and cycle.outcome == "failure":
        reasons.append("watch_voice_cycle_failed")
    if cycle and cycle.outcome == "running" and utc(cycle.deadline_at) < now:
        reasons.append("watch_voice_phase_deadline_exceeded")
    if source_counts.get("retry", 0) or source_counts.get("rejected", 0):
        reasons.append("watch_voice_source_attention")
    if expected == "persistent" and queue and cycle is None:
        reasons.append("watch_voice_worker_unobserved")
    fresh = (
        cycle is not None
        and cycle.owns_cursor
        and (active or (cycle.outcome == "success" and utc(cycle.deadline_at) > now))
    )
    incident = await session.scalar(
        select(WatchVoiceIncident)
        .where(
            WatchVoiceIncident.workspace_key == settings.gate_workspace_id,
            WatchVoiceIncident.account_id == settings.dropbox_call_account_id,
        )
        .order_by(WatchVoiceIncident.opened_at.desc(), WatchVoiceIncident.id.desc())
        .limit(1)
    )
    return {
        "schema": "watch-voice-metrics.v1",
        "observed_at": now.isoformat(),
        "expected_driver": expected,
        "monitor_host": "existing_dropbox_calls_loop",
        "monitor_host_enabled": settings.gate_dropbox_call_intake_enabled,
        "running_driver_proven": bool(
            fresh
            and cycle
            and cycle.driver == expected
            and cycle.source_sha
            and cycle.source_sha == settings.deployment_sha
        ),
        "cycle_observation_scope": "durable_shared_database_bounded_liveness_evidence",
        "observed_driver": cycle.driver if cycle else None,
        "deployment_source_sha": cycle.source_sha if cycle else None,
        "last_cycle_id": cycle.id if cycle else None,
        "cycle_phase": cycle.phase if cycle else None,
        "cycle_outcome": cycle.outcome if cycle else None,
        "liveness_evidence_valid_until": iso(cycle.deadline_at) if cycle else None,
        "source_counts": source_counts,
        "incident": (
            {
                "id": incident.id,
                "opened_at": iso(incident.opened_at),
                "observed_at": iso(incident.observed_at),
                "recovered_at": iso(incident.recovered_at),
                "active_codes": json.loads(incident.codes_json),
                "log_attempt_count": incident.log_attempt_count,
                "delivery_basis": "durable_episode_at_most_once_log_attempt_not_push_receipt",
            }
            if incident
            else None
        ),
        "last_cycle_started_at": iso(cycle.started_at) if cycle else None,
        "last_cycle_completed_at": iso(cycle.finished_at) if cycle else None,
        "last_successful_scan_at": iso(scans),
        "last_cycle_error": cycle.error_code if cycle else None,
        "last_scan_error": cycle.scan_error if cycle else None,
        "last_useful_progress_at": iso(progress),
        "queue_size": queue,
        "oldest_waiting_age_seconds": age(oldest),
        "unknown_count": unknown,
        "latency": {
            "period_start": start.isoformat(),
            "period_end": now.isoformat(),
            "sample_cap": 1000,
            "truncated": truncated,
            "phases": phases,
            "basis": "completed_records_provider_request_is_sum_of_observed_part_http_times",
        },
        "voice_cost": {
            "period": now.strftime("%Y-%m"),
            "settled_microusd": sum(r[1] or 0 for r in charges if r[0] == "settled"),
            "reserved_or_unknown_microusd": sum(
                r[2] for r in charges if r[0] in ("reserved", "unknown")
            ),
            "reservation_count": len(charges),
            "infrastructure_cost": "unknown_not_attributed",
        },
        "signal_codes": reasons,
        "notification_delivery": "existing_calls_loop_gate_log_best_effort_and_durable_status",
        "private_content_exposed": False,
    }
