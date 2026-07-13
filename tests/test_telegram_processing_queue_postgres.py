"""PostgreSQL lifecycle guards for the durable Telegram processing queue."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramProcessingDisposition,
    TelegramProcessingPlan,
)
from app.core.config import get_settings
from app.infrastructure.telegram_processing import (
    SqlTelegramProcessingQueue,
    SqlTelegramReceiptStore,
)
from app.models.domain import (
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


def _command(update_id: int, received_at: datetime) -> AcceptTelegramReceiptCommand:
    return AcceptTelegramReceiptCommand(
        bot_key="gate_m_bot",
        update_id=update_id,
        payload={"update_id": update_id},
        received_at=received_at,
    )


@pytest.mark.anyio
async def test_sql_queue_excludes_claims_and_persists_lifecycle() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL queue test")

    schema = f"queue_test_{uuid4().hex}"
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
        now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

        async with sessions() as session:
            store = SqlTelegramReceiptStore(session)
            await store.create_if_absent(_command(1, now - timedelta(seconds=2)))
            await store.create_if_absent(_command(2, now - timedelta(seconds=1)))

        async def claim_once() -> object:
            async with sessions() as session:
                return await SqlTelegramProcessingQueue(session).claim_due(now=now)

        first, second = await asyncio.gather(claim_once(), claim_once())
        assert first is not None
        assert second is not None
        assert {first.update_id, second.update_id} == {1, 2}
        assert first.outbox_event_id != second.outbox_event_id
        assert first.attempt == second.attempt == 1

        async with sessions() as session:
            await SqlTelegramProcessingQueue(session).mark_processed(
                first,
                processed_at=now + timedelta(seconds=1),
            )

        retry_at = now + timedelta(seconds=5)
        async with sessions() as session:
            await SqlTelegramProcessingQueue(session).record_failure(
                second,
                failed_at=now + timedelta(seconds=1),
                plan=TelegramProcessingPlan(
                    disposition=TelegramProcessingDisposition.RETRY,
                    next_attempt_at=retry_at,
                    attempt=1,
                    error_code="timeout",
                ),
            )

        async with sessions() as session:
            assert await SqlTelegramProcessingQueue(session).claim_due(now=now) is None
        async with sessions() as session:
            retried = await SqlTelegramProcessingQueue(session).claim_due(now=retry_at)
        assert retried is not None
        assert retried.update_id == second.update_id
        assert retried.attempt == 2

        async with sessions() as session:
            await SqlTelegramProcessingQueue(session).record_failure(
                retried,
                failed_at=retry_at,
                plan=TelegramProcessingPlan(
                    disposition=TelegramProcessingDisposition.DEAD_LETTER,
                    next_attempt_at=None,
                    attempt=2,
                    error_code="poison_update",
                ),
            )

        async with sessions() as session:
            receipts = (
                await session.execute(select(TelegramReceipt).order_by(TelegramReceipt.update_id))
            ).scalars().all()
            events = (
                await session.execute(
                    select(TelegramOutboxEvent).order_by(TelegramOutboxEvent.created_at)
                )
            ).scalars().all()

        assert {receipt.status for receipt in receipts} == {
            TelegramReceiptStatus.PROCESSED.value,
            TelegramReceiptStatus.DEAD_LETTER.value,
        }
        assert {event.status for event in events} == {
            TelegramOutboxStatus.DELIVERED.value,
            TelegramOutboxStatus.DEAD_LETTER.value,
        }
        dead_receipt = next(
            receipt
            for receipt in receipts
            if receipt.status == TelegramReceiptStatus.DEAD_LETTER.value
        )
        assert dead_receipt.attempt_count == 2
        assert dead_receipt.last_error_code == "poison_update"
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
