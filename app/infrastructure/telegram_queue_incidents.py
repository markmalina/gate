"""PostgreSQL persistence for payload-free Telegram queue incidents."""

import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.telegram_queue_alerts import (
    TelegramQueueAlertAssessment,
    TelegramQueueAlertLevel,
)
from app.application.telegram_queue_incidents import (
    TelegramQueueIncident,
    TelegramQueueIncidentReconciliation,
    TelegramQueueIncidentStatus,
    TelegramQueueIncidentStore,
    telegram_queue_incident_fingerprint,
)
from app.models.domain import TelegramQueueIncidentRecord

_ACTIVE_STATUSES = (
    TelegramQueueIncidentStatus.OPEN.value,
    TelegramQueueIncidentStatus.NOTIFIED.value,
    TelegramQueueIncidentStatus.ACKNOWLEDGED.value,
)
_LOCK_KEY = "gate.telegram_queue_incidents"


class SqlTelegramQueueIncidentStore(TelegramQueueIncidentStore):
    """Serialize and persist one aggregate queue-health incident stream."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reconcile(
        self,
        assessment: TelegramQueueAlertAssessment,
        *,
        observed_at: datetime,
    ) -> TelegramQueueIncidentReconciliation:
        try:
            await self._lock_stream()
            result = await self._session.execute(
                select(TelegramQueueIncidentRecord)
                .where(TelegramQueueIncidentRecord.status.in_(_ACTIVE_STATUSES))
                .order_by(TelegramQueueIncidentRecord.first_observed_at)
                .with_for_update()
            )
            active = list(result.scalars())

            if assessment.level == TelegramQueueAlertLevel.HEALTHY:
                for record in active:
                    self._resolve(record, observed_at=observed_at)
                reconciliation = TelegramQueueIncidentReconciliation(
                    active_incident=None,
                    should_notify=False,
                    resolved_count=len(active),
                )
                await self._session.commit()
                return reconciliation

            fingerprint = telegram_queue_incident_fingerprint(assessment)
            matching = next(
                (record for record in active if record.fingerprint == fingerprint),
                None,
            )
            resolved_count = 0
            for record in active:
                if record is not matching:
                    self._resolve(record, observed_at=observed_at)
                    resolved_count += 1

            if matching is None:
                matching = TelegramQueueIncidentRecord(
                    id=uuid.uuid4(),
                    fingerprint=fingerprint,
                    level=assessment.level.value,
                    reasons=list(assessment.reasons),
                    status=TelegramQueueIncidentStatus.OPEN.value,
                    first_observed_at=observed_at,
                    last_observed_at=observed_at,
                )
                self._session.add(matching)
                await self._session.flush()
            else:
                matching.last_observed_at = observed_at

            incident = _to_incident(matching)
            reconciliation = TelegramQueueIncidentReconciliation(
                active_incident=incident,
                should_notify=(
                    matching.status == TelegramQueueIncidentStatus.OPEN.value
                ),
                resolved_count=resolved_count,
            )
            await self._session.commit()
            return reconciliation
        except Exception:
            await self._session.rollback()
            raise

    async def mark_notified(
        self,
        incident_id: str,
        *,
        channel: str,
        notified_at: datetime,
    ) -> TelegramQueueIncident:
        normalized_channel = channel.strip()
        if not normalized_channel:
            raise ValueError("channel is required")
        try:
            record = await self._load_for_update(incident_id)
            if record.status == TelegramQueueIncidentStatus.RESOLVED.value:
                raise ValueError("a resolved incident cannot be marked notified")
            if (
                record.notification_channel is not None
                and record.notification_channel != normalized_channel
            ):
                raise ValueError("incident was notified through another channel")
            if record.status == TelegramQueueIncidentStatus.OPEN.value:
                record.status = TelegramQueueIncidentStatus.NOTIFIED.value
                record.notified_at = notified_at
                record.notification_channel = normalized_channel
            incident = _to_incident(record)
            await self._session.commit()
            return incident
        except Exception:
            await self._session.rollback()
            raise

    async def acknowledge(
        self,
        incident_id: str,
        *,
        actor: str,
        acknowledged_at: datetime,
    ) -> TelegramQueueIncident:
        normalized_actor = actor.strip()
        if not normalized_actor:
            raise ValueError("actor is required")
        try:
            record = await self._load_for_update(incident_id)
            if record.status == TelegramQueueIncidentStatus.RESOLVED.value:
                raise ValueError("a resolved incident cannot be acknowledged")
            if record.status != TelegramQueueIncidentStatus.ACKNOWLEDGED.value:
                record.status = TelegramQueueIncidentStatus.ACKNOWLEDGED.value
                record.acknowledged_at = acknowledged_at
                record.acknowledged_by = normalized_actor
            incident = _to_incident(record)
            await self._session.commit()
            return incident
        except Exception:
            await self._session.rollback()
            raise

    async def _lock_stream(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": _LOCK_KEY},
        )

    async def _load_for_update(
        self,
        incident_id: str,
    ) -> TelegramQueueIncidentRecord:
        try:
            parsed_id = uuid.UUID(incident_id)
        except ValueError as exc:
            raise ValueError("incident_id must be a UUID") from exc
        record = await self._session.scalar(
            select(TelegramQueueIncidentRecord)
            .where(TelegramQueueIncidentRecord.id == parsed_id)
            .with_for_update()
        )
        if record is None:
            raise LookupError("queue incident was not found")
        return record

    @staticmethod
    def _resolve(
        record: TelegramQueueIncidentRecord,
        *,
        observed_at: datetime,
    ) -> None:
        record.status = TelegramQueueIncidentStatus.RESOLVED.value
        record.last_observed_at = observed_at
        record.resolved_at = observed_at


def _to_incident(record: TelegramQueueIncidentRecord) -> TelegramQueueIncident:
    return TelegramQueueIncident(
        incident_id=str(record.id),
        fingerprint=record.fingerprint,
        level=TelegramQueueAlertLevel(record.level),
        reasons=tuple(record.reasons),
        observed_at=record.last_observed_at,
        status=TelegramQueueIncidentStatus(record.status),
    )
