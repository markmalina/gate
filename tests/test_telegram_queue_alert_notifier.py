"""Tests for the dormant authenticated queue alert HTTP notifier."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from app.application.telegram_queue_alerts import TelegramQueueAlertLevel
from app.application.telegram_queue_incidents import (
    TelegramQueueIncident,
    TelegramQueueIncidentStatus,
)
from app.infrastructure.telegram_queue_alert_notifier import (
    HttpTelegramQueueAlertNotifier,
)

INCIDENT = TelegramQueueIncident(
    incident_id="7f0d2a30-b613-4619-9772-13cbca6da24f",
    fingerprint="a" * 64,
    level=TelegramQueueAlertLevel.CRITICAL,
    reasons=("dead_letter_present",),
    observed_at=datetime(2026, 7, 14, 19, 0, tzinfo=UTC),
    status=TelegramQueueIncidentStatus.OPEN,
)


@pytest.mark.anyio
async def test_notifier_sends_bounded_redacted_idempotent_envelope() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = HttpTelegramQueueAlertNotifier(
            endpoint="https://alerts.internal.example/v1/incidents",
            bearer_token="test-secret",
            client=client,
        )
        await notifier.notify(INCIDENT)

    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == "Bearer test-secret"
    assert request.headers["idempotency-key"] == INCIDENT.incident_id
    assert json.loads(request.content) == {
        "schema_version": "gate.queue_incident.v1",
        "incident_id": INCIDENT.incident_id,
        "fingerprint": INCIDENT.fingerprint,
        "level": "critical",
        "reasons": ["dead_letter_present"],
        "observed_at": "2026-07-14T19:00:00+00:00",
        "status": "open",
    }
    assert b"payload" not in request.content
    assert b"account" not in request.content
    assert b"project" not in request.content
    assert b"chat" not in request.content


@pytest.mark.anyio
async def test_notifier_propagates_channel_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = HttpTelegramQueueAlertNotifier(
            endpoint="https://alerts.internal.example/v1/incidents",
            bearer_token="test-secret",
            client=client,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await notifier.notify(INCIDENT)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"endpoint": "http://alerts.example/incidents", "bearer_token": "x"},
            "absolute HTTPS URL",
        ),
        (
            {"endpoint": "https://alerts.example/incidents", "bearer_token": " "},
            "bearer_token is required",
        ),
        (
            {
                "endpoint": "https://alerts.example/incidents",
                "bearer_token": "x",
                "timeout_seconds": 31,
            },
            "timeout_seconds must be between 0 and 30",
        ),
    ],
)
def test_notifier_rejects_unsafe_composition(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        HttpTelegramQueueAlertNotifier(**kwargs)  # type: ignore[arg-type]
