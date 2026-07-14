"""Deterministic redacted alert policy for Telegram queue health."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.application.telegram_queue_maintenance import TelegramQueueHealth


class TelegramQueueAlertLevel(StrEnum):
    """Operational severity without payload or identity data."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TelegramQueueAlertThresholds:
    """Bounded thresholds applied to aggregate queue health."""

    warning_due_age_seconds: int = 120
    critical_due_age_seconds: int = 600
    warning_retry_count: int = 25

    def __post_init__(self) -> None:
        if self.warning_due_age_seconds < 1:
            raise ValueError("warning_due_age_seconds must be positive")
        if self.critical_due_age_seconds < self.warning_due_age_seconds:
            raise ValueError("critical due age must not be below warning due age")
        if self.warning_retry_count < 1:
            raise ValueError("warning_retry_count must be positive")


@dataclass(frozen=True)
class TelegramQueueAlertAssessment:
    """Stable reason codes safe for metrics, logs and alert routing."""

    level: TelegramQueueAlertLevel
    reasons: tuple[str, ...]


def assess_telegram_queue_health(
    health: TelegramQueueHealth,
    *,
    now: datetime,
    thresholds: TelegramQueueAlertThresholds | None = None,
) -> TelegramQueueAlertAssessment:
    """Assess aggregate queue state without inspecting Telegram payloads."""

    resolved = thresholds or TelegramQueueAlertThresholds()
    critical: list[str] = []
    warning: list[str] = []

    if health.dead_letter > 0:
        critical.append("dead_letter_present")
    if health.stale_processing > 0:
        critical.append("stale_processing_present")
    if health.retry_scheduled >= resolved.warning_retry_count:
        warning.append("retry_backlog_warning")

    if health.oldest_due_at is not None:
        due_age = now - health.oldest_due_at
        if due_age >= timedelta(seconds=resolved.critical_due_age_seconds):
            critical.append("due_age_critical")
        elif due_age >= timedelta(seconds=resolved.warning_due_age_seconds):
            warning.append("due_age_warning")

    if critical:
        return TelegramQueueAlertAssessment(
            level=TelegramQueueAlertLevel.CRITICAL,
            reasons=tuple(critical + warning),
        )
    if warning:
        return TelegramQueueAlertAssessment(
            level=TelegramQueueAlertLevel.WARNING,
            reasons=tuple(warning),
        )
    return TelegramQueueAlertAssessment(
        level=TelegramQueueAlertLevel.HEALTHY,
        reasons=(),
    )
