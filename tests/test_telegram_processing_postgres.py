"""PostgreSQL integration guards for atomic Telegram receipt acceptance."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import get_settings
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import AuditLog, Base, TelegramOutboxEvent, TelegramReceipt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command(
    update_id: int = 42,
    *,
    bot_key: str = "gate_m_bot",
) -> AcceptTelegramReceiptCommand:
    return AcceptTelegramReceiptCommand(
        bot_key=bot_key,
        update_id=update_id,
        payload={"update_id": update_id, "business_message": {}},
        received_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_sql_store_is_atomic_and_idempotent_under_concurrency() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL receipt-store test")

    schema = f"receipt_test_{uuid4().hex}"
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

        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def accept_once() -> TelegramReceiptAcceptance:
            async with session_factory() as session:
                return await SqlTelegramReceiptStore(session).create_if_absent(_command())

        first, second = await asyncio.gather(accept_once(), accept_once())

        assert {first.duplicate, second.duplicate} == {False, True}
        assert first.receipt_id == second.receipt_id
        assert first.outbox_event_id == second.outbox_event_id

        async with session_factory() as session:
            receipt_count = await session.scalar(select(func.count()).select_from(TelegramReceipt))
            outbox_count = await session.scalar(
                select(func.count()).select_from(TelegramOutboxEvent)
            )
            receipt = (await session.execute(select(TelegramReceipt))).scalar_one()
            outbox = (await session.execute(select(TelegramOutboxEvent))).scalar_one()

        assert receipt_count == 1
        assert outbox_count == 1
        assert receipt.payload == _command().payload
        assert outbox.receipt_id == receipt.id
        assert outbox.available_at == _command().received_at
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_shared_session_remains_usable_after_first_and_duplicate_acceptance() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL receipt-store test")

    schema = f"receipt_commit_test_{uuid4().hex}"
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

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        accepted_command = _command(84, bot_key="telegram_business")
        async with session_factory() as session:
            store = SqlTelegramReceiptStore(session)
            first = await store.create_if_absent(accepted_command)
            session.add(
                AuditLog(
                    actor="test",
                    action="legacy_after_first_acceptance",
                    metadata_={},
                )
            )
            await session.commit()

            duplicate = await store.create_if_absent(accepted_command)
            session.add(
                AuditLog(
                    actor="test",
                    action="legacy_after_duplicate_acceptance",
                    metadata_={},
                )
            )
            await session.commit()

        async with session_factory() as session:
            receipts = (await session.execute(select(TelegramReceipt))).scalars().all()
            outboxes = (await session.execute(select(TelegramOutboxEvent))).scalars().all()
            audit_actions = (
                (await session.execute(select(AuditLog.action).order_by(AuditLog.action)))
                .scalars()
                .all()
            )

        assert len(receipts) == 1
        assert len(outboxes) == 1
        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert duplicate.receipt_id == first.receipt_id
        assert duplicate.outbox_event_id == first.outbox_event_id
        assert str(receipts[0].id) == first.receipt_id
        assert receipts[0].bot_key == "telegram_business"
        assert receipts[0].update_id == 84
        assert outboxes[0].receipt_id == receipts[0].id
        assert str(outboxes[0].id) == first.outbox_event_id
        assert audit_actions == [
            "legacy_after_duplicate_acceptance",
            "legacy_after_first_acceptance",
        ]
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
