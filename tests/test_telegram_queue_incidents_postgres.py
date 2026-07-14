"""PostgreSQL guards for Telegram queue incident persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_queue_alerts import (
    TelegramQueueAlertAssessment,
    TelegramQueueAlertLevel,
)
from app.application.telegram_queue_incidents import TelegramQueueIncidentStatus
from app.core.config import get_settings
from app.infrastructure.telegram_queue_incidents import (
    SqlTelegramQueueIncidentStore,
)
from app.models.domain import Base, TelegramQueueIncidentRecord
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 7, 14, 18, 0, tzinfo=UTC)
WARNING = TelegramQueueAlertAssessment(
    level=TelegramQueueAlertLevel.WARNING,
    reasons=("retry_backlog_warning",),
)
CRITICAL = TelegramQueueAlertAssessment(
    level=TelegramQueueAlertLevel.CRITICAL,
    reasons=("dead_letter_present",),
)
HEALTHY = TelegramQueueAlertAssessment(
    level=TelegramQueueAlertLevel.HEALTHY,
    reasons=(),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_incident_lifecycle_is_durable_idempotent_and_payload_free() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL incident test")

    schema = f"queue_incidents_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def observe_warning() -> str:
            async with sessions() as session:
                reconciliation = await SqlTelegramQueueIncidentStore(
                    session
                ).reconcile(WARNING, observed_at=NOW)
                assert reconciliation.active_incident is not None
                return reconciliation.active_incident.incident_id

        first_id, concurrent_id = await asyncio.gather(
            observe_warning(),
            observe_warning(),
        )
        assert first_id == concurrent_id

        async with sessions() as session:
            store = SqlTelegramQueueIncidentStore(session)
            notified = await store.mark_notified(
                first_id,
                channel="operator",
                notified_at=NOW + timedelta(seconds=1),
            )
            repeated = await store.mark_notified(
                first_id,
                channel="operator",
                notified_at=NOW + timedelta(seconds=2),
            )
        assert notified.status == TelegramQueueIncidentStatus.NOTIFIED
        assert repeated.status == TelegramQueueIncidentStatus.NOTIFIED

        async with sessions() as session:
            stable = await SqlTelegramQueueIncidentStore(session).reconcile(
                WARNING,
                observed_at=NOW + timedelta(minutes=1),
            )
        assert stable.active_incident is not None
        assert stable.active_incident.incident_id == first_id
        assert stable.should_notify is False

        async with sessions() as session:
            acknowledged = await SqlTelegramQueueIncidentStore(session).acknowledge(
                first_id,
                actor="on-call",
                acknowledged_at=NOW + timedelta(minutes=2),
            )
        assert acknowledged.status == TelegramQueueIncidentStatus.ACKNOWLEDGED

        async with sessions() as session:
            escalated = await SqlTelegramQueueIncidentStore(session).reconcile(
                CRITICAL,
                observed_at=NOW + timedelta(minutes=3),
            )
        assert escalated.active_incident is not None
        assert escalated.active_incident.incident_id != first_id
        assert escalated.should_notify is True
        assert escalated.resolved_count == 1

        async with sessions() as session:
            recovered = await SqlTelegramQueueIncidentStore(session).reconcile(
                HEALTHY,
                observed_at=NOW + timedelta(minutes=4),
            )
        assert recovered.active_incident is None
        assert recovered.resolved_count == 1

        async with sessions() as session:
            records = (
                await session.execute(
                    select(TelegramQueueIncidentRecord).order_by(
                        TelegramQueueIncidentRecord.first_observed_at
                    )
                )
            ).scalars().all()
        assert [record.status for record in records] == [
            TelegramQueueIncidentStatus.RESOLVED.value,
            TelegramQueueIncidentStatus.RESOLVED.value,
        ]
        assert records[0].acknowledged_by == "on-call"
        assert records[0].notification_channel == "operator"
        assert not hasattr(records[0], "payload")
        assert not hasattr(records[0], "account_id")
        assert not hasattr(records[0], "project_id")
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
