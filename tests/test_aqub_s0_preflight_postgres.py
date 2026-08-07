"""PostgreSQL proof that AQUB preflight receipts never enter the common queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.aqub_s0_preflight import (
    AQUBPreflightCommand,
    AQUBPreflightRole,
    AQUBStoreDisposition,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.core.config import get_settings
from app.infrastructure.aqub_s0_preflight import (
    AQUB_PREFLIGHT_BOT_KEY,
    SqlAQUBPreflightReceiptStore,
)
from app.infrastructure.telegram_processing import SqlTelegramProcessingQueue
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramReceipt,
    TelegramReceiptStatus,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command() -> AQUBPreflightCommand:
    occurred_at = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)
    payload = {
        "update_id": 987654321,
        "message": {
            "date": int(occurred_at.timestamp()),
            "chat": {"id": -1007001, "type": "supergroup"},
            "from": {"id": 7003, "is_bot": False},
            "text": "/aqub_ping",
        },
    }
    return AQUBPreflightCommand(
        source="telegram_bot_api",
        external_id=str(payload["update_id"]),
        payload_sha256=canonical_telegram_payload_digest(payload),
        payload=payload,
        chat_id="-1007001",
        actor_id="7003",
        role=AQUBPreflightRole.ALPHA,
        command="/aqub_ping",
        occurred_at=occurred_at,
        received_at=occurred_at + timedelta(seconds=1),
    )


@pytest.mark.anyio
async def test_terminal_receipt_is_idempotent_and_never_queued() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB terminal receipt test")

    schema = f"aqub_receipt_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(TelegramReceipt.__table__.create)
            await connection.run_sync(TelegramOutboxEvent.__table__.create)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            store = SqlAQUBPreflightReceiptStore(session)
            first = await store.accept(_command())
            duplicate = await store.accept(_command())

        assert first.disposition is AQUBStoreDisposition.CREATED
        assert duplicate.disposition is AQUBStoreDisposition.DUPLICATE
        assert duplicate.command_receipt_id == first.command_receipt_id
        assert duplicate.root_event_id == first.root_event_id

        async with session_factory() as session:
            receipt_count = await session.scalar(
                select(func.count())
                .select_from(TelegramReceipt)
                .where(TelegramReceipt.bot_key == AQUB_PREFLIGHT_BOT_KEY)
            )
            outbox_count = await session.scalar(
                select(func.count())
                .select_from(TelegramOutboxEvent)
                .join(TelegramReceipt)
                .where(TelegramReceipt.bot_key == AQUB_PREFLIGHT_BOT_KEY)
            )
            receipt = (
                await session.execute(
                    select(TelegramReceipt).where(
                        TelegramReceipt.bot_key == AQUB_PREFLIGHT_BOT_KEY
                    )
                )
            ).scalar_one()
            receipt_status = receipt.status
            receipt_processed_at = receipt.processed_at
            queued = await SqlTelegramProcessingQueue(session).claim_due(
                now=_command().received_at + timedelta(seconds=1)
            )

        assert receipt_count == 1
        assert outbox_count == 0
        assert receipt_status == TelegramReceiptStatus.PROCESSED.value
        assert receipt_processed_at is not None
        assert queued is None
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
