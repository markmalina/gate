"""Application contracts for durable redacted Telegram queue incidents."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from app.application.telegram_queue_alerts import (
    TelegramQueueAlertAssessment,
    TelegramQueueAlertLevel,
)


class TelegramQueueIncidentStatus(StrEnum):
    """Durable lifecycle of one aggregate queue-health incident."""

    OPEN = "open"
    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class TelegramQueueAlertDeliveryDisposition(StrEnum):
    """Redacted outcome of one alert observation."""

    NONE = "none"
    DISABLED = "disabled"
    DELIVERED = "delivered"


@dataclass(frozen=True)
class TelegramQueueIncident:
    """Payload-free incident safe to pass to an operator notification adapter."""

    incident_id: str
    fingerprint: str
    level: TelegramQueueAlertLevel
    reasons: tuple[str, ...]
    observed_at: datetime
    status: TelegramQueueIncidentStatus


@dataclass(frozen=True)
class TelegramQueueIncidentReconciliation:
    """Store decision after reconciling the latest aggregate assessment."""

    active_incident: TelegramQueueIncident | None
    should_notify: bool
    resolved_count: int = 0


@dataclass(frozen=True)
class TelegramQueueAlertDeliveryResult:
    """Observable result without Telegram payloads, peers or account identifiers."""

    disposition: TelegramQueueAlertDeliveryDisposition
    incident_id: str | None = None


def telegram_queue_incident_fingerprint(
    assessment: TelegramQueueAlertAssessment,
) -> str:
    """Create a stable identity from severity and bounded reason codes only."""

    reasons = ",".join(sorted(assessment.reasons))
    material = f"{assessment.level.value}|{reasons}".encode()
    return sha256(material).hexdigest()


class TelegramQueueIncidentStore(Protocol):
    """Persist incident transitions without owning notification delivery."""

    async def reconcile(
        self,
        assessment: TelegramQueueAlertAssessment,
        *,
        observed_at: datetime,
    ) -> TelegramQueueIncidentReconciliation:
        """Open, reuse or resolve incidents for the latest assessment."""

    async def mark_notified(
        self,
        incident_id: str,
        *,
        channel: str,
        notified_at: datetime,
    ) -> TelegramQueueIncident:
        """Record delivery only after the notifier returns successfully."""

    async def acknowledge(
        self,
        incident_id: str,
        *,
        actor: str,
        acknowledged_at: datetime,
    ) -> TelegramQueueIncident:
        """Record an explicit operator acknowledgement."""


class TelegramQueueAlertNotifier(Protocol):
    """Deliver only the redacted incident envelope to an operator channel."""

    async def notify(self, incident: TelegramQueueIncident) -> None:
        """Return only after the channel accepts the notification."""


class ObserveTelegramQueueAlertService:
    """Reconcile an assessment and optionally deliver a new incident."""

    def __init__(
        self,
        *,
        store: TelegramQueueIncidentStore,
        notifier: TelegramQueueAlertNotifier | None = None,
        delivery_enabled: bool = False,
        channel: str = "",
    ) -> None:
        if delivery_enabled and notifier is None:
            raise ValueError("notifier is required when delivery is enabled")
        if delivery_enabled and not channel.strip():
            raise ValueError("channel is required when delivery is enabled")
        self._store = store
        self._notifier = notifier
        self._delivery_enabled = delivery_enabled
        self._channel = channel.strip()

    async def execute(
        self,
        assessment: TelegramQueueAlertAssessment,
        *,
        observed_at: datetime,
    ) -> TelegramQueueAlertDeliveryResult:
        reconciliation = await self._store.reconcile(
            assessment,
            observed_at=observed_at,
        )
        incident = reconciliation.active_incident
        if incident is None or not reconciliation.should_notify:
            return TelegramQueueAlertDeliveryResult(
                disposition=TelegramQueueAlertDeliveryDisposition.NONE,
            )
        if not self._delivery_enabled:
            return TelegramQueueAlertDeliveryResult(
                disposition=TelegramQueueAlertDeliveryDisposition.DISABLED,
                incident_id=incident.incident_id,
            )

        notifier = self._notifier
        if notifier is None:  # Defensive guard for future composition changes.
            raise RuntimeError("notification delivery is not configured")
        await notifier.notify(incident)
        await self._store.mark_notified(
            incident.incident_id,
            channel=self._channel,
            notified_at=observed_at,
        )
        return TelegramQueueAlertDeliveryResult(
            disposition=TelegramQueueAlertDeliveryDisposition.DELIVERED,
            incident_id=incident.incident_id,
        )


class AcknowledgeTelegramQueueIncidentService:
    """Validate and record an explicit operator acknowledgement."""

    def __init__(self, store: TelegramQueueIncidentStore) -> None:
        self._store = store

    async def execute(
        self,
        incident_id: str,
        *,
        actor: str,
        acknowledged_at: datetime,
    ) -> TelegramQueueIncident:
        normalized_incident_id = incident_id.strip()
        normalized_actor = actor.strip()
        if not normalized_incident_id:
            raise ValueError("incident_id is required")
        if not normalized_actor:
            raise ValueError("actor is required")
        return await self._store.acknowledge(
            normalized_incident_id,
            actor=normalized_actor,
            acknowledged_at=acknowledged_at,
        )
