"""Tests for durable redacted Telegram queue incident contracts."""

from datetime import UTC, datetime

import pytest
from app.application.telegram_queue_alerts import (
    TelegramQueueAlertAssessment,
    TelegramQueueAlertLevel,
)
from app.application.telegram_queue_incidents import (
    AcknowledgeTelegramQueueIncidentService,
    ObserveTelegramQueueAlertService,
    TelegramQueueAlertDeliveryDisposition,
    TelegramQueueAlertNotifier,
    TelegramQueueIncident,
    TelegramQueueIncidentReconciliation,
    TelegramQueueIncidentStatus,
    telegram_queue_incident_fingerprint,
)

NOW = datetime(2026, 7, 14, 14, 0, tzinfo=UTC)
WARNING = TelegramQueueAlertAssessment(
    level=TelegramQueueAlertLevel.WARNING,
    reasons=("retry_backlog_warning", "due_age_warning"),
)


def _incident(
    *,
    status: TelegramQueueIncidentStatus = TelegramQueueIncidentStatus.OPEN,
) -> TelegramQueueIncident:
    return TelegramQueueIncident(
        incident_id="incident-1",
        fingerprint=telegram_queue_incident_fingerprint(WARNING),
        level=WARNING.level,
        reasons=WARNING.reasons,
        observed_at=NOW,
        status=status,
    )


class InMemoryIncidentStore:
    def __init__(self, reconciliation: TelegramQueueIncidentReconciliation) -> None:
        self.reconciliation = reconciliation
        self.reconciled: list[tuple[TelegramQueueAlertAssessment, datetime]] = []
        self.notified: list[tuple[str, str, datetime]] = []
        self.acknowledged: list[tuple[str, str, datetime]] = []

    async def reconcile(
        self,
        assessment: TelegramQueueAlertAssessment,
        *,
        observed_at: datetime,
    ) -> TelegramQueueIncidentReconciliation:
        self.reconciled.append((assessment, observed_at))
        return self.reconciliation

    async def mark_notified(
        self,
        incident_id: str,
        *,
        channel: str,
        notified_at: datetime,
    ) -> TelegramQueueIncident:
        self.notified.append((incident_id, channel, notified_at))
        return _incident(status=TelegramQueueIncidentStatus.NOTIFIED)

    async def acknowledge(
        self,
        incident_id: str,
        *,
        actor: str,
        acknowledged_at: datetime,
    ) -> TelegramQueueIncident:
        self.acknowledged.append((incident_id, actor, acknowledged_at))
        return _incident(status=TelegramQueueIncidentStatus.ACKNOWLEDGED)


class RecordingNotifier(TelegramQueueAlertNotifier):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.incidents: list[TelegramQueueIncident] = []

    async def notify(self, incident: TelegramQueueIncident) -> None:
        self.incidents.append(incident)
        if self.fail:
            raise RuntimeError("channel unavailable")


@pytest.mark.anyio
async def test_healthy_reconciliation_does_not_notify() -> None:
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(
            active_incident=None,
            should_notify=False,
            resolved_count=1,
        )
    )
    notifier = RecordingNotifier()
    service = ObserveTelegramQueueAlertService(
        store=store,
        notifier=notifier,
        delivery_enabled=True,
        channel="operator",
    )
    healthy = TelegramQueueAlertAssessment(
        level=TelegramQueueAlertLevel.HEALTHY,
        reasons=(),
    )

    result = await service.execute(healthy, observed_at=NOW)

    assert result.disposition == TelegramQueueAlertDeliveryDisposition.NONE
    assert store.reconciled == [(healthy, NOW)]
    assert notifier.incidents == []


@pytest.mark.anyio
async def test_disabled_delivery_reconciles_without_external_call() -> None:
    incident = _incident()
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(
            active_incident=incident,
            should_notify=True,
        )
    )
    service = ObserveTelegramQueueAlertService(store=store)

    result = await service.execute(WARNING, observed_at=NOW)

    assert result.disposition == TelegramQueueAlertDeliveryDisposition.DISABLED
    assert result.incident_id == incident.incident_id
    assert store.notified == []


@pytest.mark.anyio
async def test_enabled_delivery_notifies_then_marks_incident() -> None:
    incident = _incident()
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(
            active_incident=incident,
            should_notify=True,
        )
    )
    notifier = RecordingNotifier()
    service = ObserveTelegramQueueAlertService(
        store=store,
        notifier=notifier,
        delivery_enabled=True,
        channel=" operator ",
    )

    result = await service.execute(WARNING, observed_at=NOW)

    assert result.disposition == TelegramQueueAlertDeliveryDisposition.DELIVERED
    assert notifier.incidents == [incident]
    assert store.notified == [(incident.incident_id, "operator", NOW)]


@pytest.mark.anyio
async def test_already_notified_incident_is_not_delivered_again() -> None:
    incident = _incident(status=TelegramQueueIncidentStatus.NOTIFIED)
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(
            active_incident=incident,
            should_notify=False,
        )
    )
    notifier = RecordingNotifier()
    service = ObserveTelegramQueueAlertService(
        store=store,
        notifier=notifier,
        delivery_enabled=True,
        channel="operator",
    )

    result = await service.execute(WARNING, observed_at=NOW)

    assert result.disposition == TelegramQueueAlertDeliveryDisposition.NONE
    assert notifier.incidents == []


@pytest.mark.anyio
async def test_notification_failure_does_not_mark_incident_notified() -> None:
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(
            active_incident=_incident(),
            should_notify=True,
        )
    )
    service = ObserveTelegramQueueAlertService(
        store=store,
        notifier=RecordingNotifier(fail=True),
        delivery_enabled=True,
        channel="operator",
    )

    with pytest.raises(RuntimeError, match="channel unavailable"):
        await service.execute(WARNING, observed_at=NOW)

    assert store.notified == []


@pytest.mark.anyio
async def test_acknowledgement_is_normalized_and_delegated() -> None:
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(None, False)
    )
    service = AcknowledgeTelegramQueueIncidentService(store)

    result = await service.execute(
        " incident-1 ",
        actor=" on-call ",
        acknowledged_at=NOW,
    )

    assert result.status == TelegramQueueIncidentStatus.ACKNOWLEDGED
    assert store.acknowledged == [("incident-1", "on-call", NOW)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("incident_id", "actor", "message"),
    [
        (" ", "on-call", "incident_id is required"),
        ("incident-1", " ", "actor is required"),
    ],
)
async def test_acknowledgement_rejects_blank_identity(
    incident_id: str,
    actor: str,
    message: str,
) -> None:
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(None, False)
    )
    service = AcknowledgeTelegramQueueIncidentService(store)

    with pytest.raises(ValueError, match=message):
        await service.execute(incident_id, actor=actor, acknowledged_at=NOW)

    assert store.acknowledged == []


def test_fingerprint_is_stable_and_reason_order_independent() -> None:
    reordered = TelegramQueueAlertAssessment(
        level=WARNING.level,
        reasons=tuple(reversed(WARNING.reasons)),
    )

    fingerprint = telegram_queue_incident_fingerprint(WARNING)

    assert fingerprint == telegram_queue_incident_fingerprint(reordered)
    assert len(fingerprint) == 64
    assert "retry" not in fingerprint


@pytest.mark.parametrize(
    ("notifier", "channel", "message"),
    [
        (None, "operator", "notifier is required"),
        (RecordingNotifier(), " ", "channel is required"),
    ],
)
def test_enabled_delivery_requires_complete_composition(
    notifier: TelegramQueueAlertNotifier | None,
    channel: str,
    message: str,
) -> None:
    store = InMemoryIncidentStore(
        TelegramQueueIncidentReconciliation(None, False)
    )

    with pytest.raises(ValueError, match=message):
        ObserveTelegramQueueAlertService(
            store=store,
            notifier=notifier,
            delivery_enabled=True,
            channel=channel,
        )
