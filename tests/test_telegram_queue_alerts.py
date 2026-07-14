"""Tests for redacted deterministic Telegram queue alerts."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.telegram_queue_alerts import (
    TelegramQueueAlertLevel,
    TelegramQueueAlertThresholds,
    assess_telegram_queue_health,
)
from app.application.telegram_queue_maintenance import TelegramQueueHealth

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _health(**overrides: object) -> TelegramQueueHealth:
    values = {
        "pending": 0,
        "processing": 0,
        "retry_scheduled": 0,
        "delivered": 0,
        "dead_letter": 0,
        "stale_processing": 0,
        "oldest_due_at": None,
        **overrides,
    }
    return TelegramQueueHealth(**values)


def test_empty_or_fresh_queue_is_healthy() -> None:
    empty = assess_telegram_queue_health(_health(), now=NOW)
    fresh = assess_telegram_queue_health(
        _health(pending=1, oldest_due_at=NOW - timedelta(seconds=30)),
        now=NOW,
    )

    assert empty.level == TelegramQueueAlertLevel.HEALTHY
    assert empty.reasons == ()
    assert fresh.level == TelegramQueueAlertLevel.HEALTHY


def test_due_age_and_retry_backlog_raise_warning() -> None:
    assessment = assess_telegram_queue_health(
        _health(
            retry_scheduled=25,
            oldest_due_at=NOW - timedelta(seconds=120),
        ),
        now=NOW,
    )

    assert assessment.level == TelegramQueueAlertLevel.WARNING
    assert assessment.reasons == (
        "retry_backlog_warning",
        "due_age_warning",
    )


@pytest.mark.parametrize(
    ("health", "reason"),
    [
        (_health(dead_letter=1), "dead_letter_present"),
        (_health(stale_processing=1), "stale_processing_present"),
        (
            _health(oldest_due_at=NOW - timedelta(seconds=600)),
            "due_age_critical",
        ),
    ],
)
def test_terminal_stale_or_old_due_work_is_critical(
    health: TelegramQueueHealth,
    reason: str,
) -> None:
    assessment = assess_telegram_queue_health(health, now=NOW)

    assert assessment.level == TelegramQueueAlertLevel.CRITICAL
    assert reason in assessment.reasons


@pytest.mark.parametrize(
    "thresholds",
    [
        TelegramQueueAlertThresholds,
    ],
)
def test_threshold_defaults_are_bounded(
    thresholds: type[TelegramQueueAlertThresholds],
) -> None:
    assert thresholds().warning_due_age_seconds > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"warning_due_age_seconds": 0},
        {"warning_due_age_seconds": 120, "critical_due_age_seconds": 119},
        {"warning_retry_count": 0},
    ],
)
def test_invalid_thresholds_are_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        TelegramQueueAlertThresholds(**kwargs)
