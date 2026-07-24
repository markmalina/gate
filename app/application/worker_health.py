"""Redacted in-process worker heartbeat and degradation evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

TELEGRAM_WORKER = "telegram"
REPLY_SEQUENCE_WORKER = "reply_sequence"


class WorkerRuntimeState(StrEnum):
    """Bounded worker state exposed to the owner-only operations endpoint."""

    DISABLED = "disabled"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class WorkerHealthSnapshot:
    """Redacted observation for one configured worker."""

    name: str
    enabled: bool
    state: WorkerRuntimeState
    consecutive_failures: int
    heartbeat_age_seconds: float | None
    last_error_code: str | None
    last_failure_age_seconds: float | None


@dataclass(frozen=True, slots=True)
class RuntimeHealthSnapshot:
    """Aggregate status without payloads, identities, or exception details."""

    status: Literal["healthy", "degraded"]
    workers: tuple[WorkerHealthSnapshot, ...]


class WorkerHealthReporter(Protocol):
    """Port used by worker loops to record one bounded iteration outcome."""

    def record_success(self, name: str, *, observed_at: datetime) -> None:
        """Record one completed iteration, including an idle iteration."""

    def record_failure(
        self,
        name: str,
        *,
        error_code: str,
        observed_at: datetime,
    ) -> None:
        """Record a stable redacted failure code, never exception text."""


@dataclass(slots=True)
class _WorkerObservation:
    enabled: bool
    last_heartbeat_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_error_code: str | None = None


class WorkerHealthRegistry:
    """Event-loop-local worker health registry."""

    def __init__(self, *, heartbeat_stale_after_seconds: float = 60.0) -> None:
        if heartbeat_stale_after_seconds <= 0:
            raise ValueError("heartbeat stale threshold must be positive")
        self._heartbeat_stale_after_seconds = heartbeat_stale_after_seconds
        self._workers: dict[str, _WorkerObservation] = {}

    def register(self, name: str, *, enabled: bool) -> None:
        """Register each configured worker once before its task starts."""

        if not name or name in self._workers:
            raise ValueError("worker must have one unique non-empty name")
        self._workers[name] = _WorkerObservation(enabled=enabled)

    def record_success(self, name: str, *, observed_at: datetime) -> None:
        observation = self._require_enabled(name)
        observation.last_heartbeat_at = _as_utc(observed_at)
        observation.consecutive_failures = 0

    def record_failure(
        self,
        name: str,
        *,
        error_code: str,
        observed_at: datetime,
    ) -> None:
        observation = self._require_enabled(name)
        observed = _as_utc(observed_at)
        observation.last_heartbeat_at = observed
        observation.last_failure_at = observed
        observation.consecutive_failures += 1
        observation.last_error_code = _safe_error_code(error_code)

    def snapshot(self, *, observed_at: datetime | None = None) -> RuntimeHealthSnapshot:
        now = _as_utc(observed_at or datetime.now(UTC))
        workers = tuple(
            self._worker_snapshot(name, observation, observed_at=now)
            for name, observation in sorted(self._workers.items())
        )
        enabled_workers = tuple(worker for worker in workers if worker.enabled)
        aggregate: Literal["healthy", "degraded"] = (
            "degraded"
            if not enabled_workers
            or any(
                worker.state is not WorkerRuntimeState.HEALTHY
                for worker in enabled_workers
            )
            else "healthy"
        )
        return RuntimeHealthSnapshot(status=aggregate, workers=workers)

    def _worker_snapshot(
        self,
        name: str,
        observation: _WorkerObservation,
        *,
        observed_at: datetime,
    ) -> WorkerHealthSnapshot:
        if not observation.enabled:
            return WorkerHealthSnapshot(
                name=name,
                enabled=False,
                state=WorkerRuntimeState.DISABLED,
                consecutive_failures=0,
                heartbeat_age_seconds=None,
                last_error_code=None,
                last_failure_age_seconds=None,
            )
        if observation.last_heartbeat_at is None:
            return WorkerHealthSnapshot(
                name=name,
                enabled=True,
                state=WorkerRuntimeState.STARTING,
                consecutive_failures=observation.consecutive_failures,
                heartbeat_age_seconds=None,
                last_error_code=observation.last_error_code,
                last_failure_age_seconds=None,
            )
        age = max(0.0, (observed_at - observation.last_heartbeat_at).total_seconds())
        failure_age = (
            max(0.0, (observed_at - observation.last_failure_at).total_seconds())
            if observation.last_failure_at is not None
            else None
        )
        stale = age > self._heartbeat_stale_after_seconds
        degraded = stale or observation.consecutive_failures > 0
        return WorkerHealthSnapshot(
            name=name,
            enabled=True,
            state=(
                WorkerRuntimeState.DEGRADED
                if degraded
                else WorkerRuntimeState.HEALTHY
            ),
            consecutive_failures=observation.consecutive_failures,
            heartbeat_age_seconds=age,
            last_error_code=(
                "worker_heartbeat_stale"
                if stale
                else observation.last_error_code
            ),
            last_failure_age_seconds=failure_age,
        )

    def _require_enabled(self, name: str) -> _WorkerObservation:
        observation = self._workers.get(name)
        if observation is None:
            raise ValueError("worker must be registered before recording health")
        if not observation.enabled:
            raise ValueError("disabled worker cannot record runtime health")
        return observation


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("worker health timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _safe_error_code(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value):
        return value
    return "worker_iteration_failed"
