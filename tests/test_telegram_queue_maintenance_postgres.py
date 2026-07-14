"""PostgreSQL guards for Telegram queue health and stale recovery."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
)
from app.core.config import get_settings
from app.infrastructure.telegram_queue_maintenance import (
    SqlTelegramQueueHealthReader,
    SqlTelegramQueueMaintenance,
)
from app.models.domain import (
    AuditLog,
    Base,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_and_recovery_are_bounded_and_redacted() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL queue maintenance test")

    schema = f"queue_maintenance_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

        async with sessions() as session:
            for update_id, attempt_count in ((1, 2), (2, 5)):
                receipt = TelegramReceipt(
                    id=uuid4(),
                    bot_key="gate_m_bot",
                    update_id=update_id,
                    payload={"update_id": update_id, "text": "must-not-leak"},
                    status=TelegramReceiptStatus.PROCESSING.value,
                    attempt_count=attempt_count,
                    received_at=now - timedelta(minutes=10),
                    processing_started_at=now - timedelta(minutes=9),
                )
                session.add(receipt)
                await session.flush()
                session.add(
                    TelegramOutboxEvent(
                        id=uuid4(),
                        receipt_id=receipt.id,
                        status=TelegramOutboxStatus.PROCESSING.value,
                        attempt_count=attempt_count,
                        available_at=now - timedelta(minutes=10),
                        locked_at=now - timedelta(minutes=9),
                    )
                )
            await session.commit()

        stale_before = now - timedelta(minutes=5)
        async with sessions() as session:
            health = await SqlTelegramQueueHealthReader(session).read(
                now=now,
                stale_before=stale_before,
            )
        assert health.processing == 2
        assert health.stale_processing == 2
        assert not hasattr(health, "payload")

        async with sessions() as session:
            recovery = await SqlTelegramQueueMaintenance(session).recover_stale(
                RecoverStaleTelegramClaimsCommand(
                    stale_before=stale_before,
                    recovered_at=now,
                    max_attempts=5,
                    limit=100,
                )
            )
        assert recovery.recovered == 2
        assert recovery.retry_scheduled == 1
        assert recovery.dead_lettered == 1

        async with sessions() as session:
            receipts = (
                await session.execute(select(TelegramReceipt).order_by(TelegramReceipt.update_id))
            ).scalars().all()
            events = (
                await session.execute(
                    select(TelegramOutboxEvent).order_by(TelegramOutboxEvent.attempt_count)
                )
            ).scalars().all()
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action
                        == "telegram_queue_stale_claims_recovered"
                    )
                )
            ).scalar_one()
        assert [receipt.status for receipt in receipts] == [
            TelegramReceiptStatus.RECEIVED.value,
            TelegramReceiptStatus.DEAD_LETTER.value,
        ]
        assert [event.status for event in events] == [
            TelegramOutboxStatus.RETRY_SCHEDULED.value,
            TelegramOutboxStatus.DEAD_LETTER.value,
        ]
        assert events[0].available_at == now
        assert events[0].locked_at is None
        assert receipts[1].last_error_code == "worker_lease_expired"
        assert audit.actor == "gate-telegram-worker"
        assert audit.account_id is None
        assert audit.project_id is None
        assert audit.metadata_ == {
            "recovered": 2,
            "retry_scheduled": 1,
            "dead_lettered": 1,
            "max_attempts": 5,
            "limit": 100,
        }
        assert "payload" not in audit.metadata_
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
