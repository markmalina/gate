"""PostgreSQL proofs for DB-owned AQUB provider installation binding."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_installation import (
    AQUB_RECEIPT_BOT_KEY,
    load_aqub_production_binding,
)
from app.models.aqub import AQUBInstallationBinding
from app.models.domain import TelegramReceipt, TelegramReceiptStatus
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_CHAT_ID = "-1001234567890"
TEST_ALPHA_USER_ID = "123456789"
TEST_SIGNING_KEY = "0123456789abcdef0123456789abcdef-binding"
TEST_READY_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        GATE_AQUB_RECEIPT_SIGNING_KEY=TEST_SIGNING_KEY,
    )


def _ping_payload(
    *,
    chat_id: int = int(TEST_CHAT_ID),
    sender_id: int = int(TEST_ALPHA_USER_ID),
    text_value: str = "/aqub_ping",
) -> dict[str, object]:
    return {
        "update_id": 901,
        "message": {
            "message_id": 902,
            "date": int(TEST_READY_AT.timestamp()),
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {"id": sender_id, "is_bot": False},
            "text": text_value,
        },
    }


async def _schema_engine(database_url: str):
    schema = f"aqub_binding_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(TelegramReceipt.__table__.create)
        await connection.run_sync(AQUBInstallationBinding.__table__.create)
    return schema, admin_engine, engine


async def _drop_schema(schema: str, admin_engine, engine) -> None:
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


async def _insert_receipt(
    session,
    *,
    update_id: int,
    payload: dict[str, object],
    processed_at: datetime,
) -> TelegramReceipt:
    receipt = TelegramReceipt(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        update_id=update_id,
        payload=payload,
        status=TelegramReceiptStatus.PROCESSED.value,
        received_at=processed_at,
        processed_at=processed_at,
    )
    session.add(receipt)
    await session.commit()
    return receipt


@pytest.mark.anyio
async def test_binding_fails_closed_without_historical_terminal_ping() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB installation binding test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            with pytest.raises(ValueError, match="evidence is unavailable"):
                await load_aqub_production_binding(session, _settings())
            count = await session.scalar(select(func.count()).select_from(AQUBInstallationBinding))
            assert count == 0
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_historical_ping_bootstraps_once_and_newer_receipt_cannot_rebind() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB installation binding test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            evidence = await _insert_receipt(
                session,
                update_id=901,
                payload=_ping_payload(),
                processed_at=TEST_READY_AT,
            )
            binding = await load_aqub_production_binding(session, _settings())
            assert binding.group_chat_id == TEST_CHAT_ID
            assert binding.alpha_user_id == TEST_ALPHA_USER_ID
            stored = await session.scalar(select(AQUBInstallationBinding))
            assert stored is not None
            assert stored.evidence_receipt_id == evidence.id

        other_chat = -1009876543210
        other_actor = 987654321
        async with sessions() as session:
            await _insert_receipt(
                session,
                update_id=903,
                payload=_ping_payload(chat_id=other_chat, sender_id=other_actor),
                processed_at=TEST_READY_AT + timedelta(minutes=1),
            )
            reloaded = await load_aqub_production_binding(session, _settings())
            assert reloaded.group_chat_id == TEST_CHAT_ID
            assert reloaded.alpha_user_id == TEST_ALPHA_USER_ID
            count = await session.scalar(select(func.count()).select_from(AQUBInstallationBinding))
            assert count == 1
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_concurrent_bootstrap_converges_on_one_binding() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB installation binding test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await _insert_receipt(
                session,
                update_id=904,
                payload=_ping_payload(),
                processed_at=TEST_READY_AT,
            )

        async def load_once():
            async with sessions() as session:
                return await load_aqub_production_binding(session, _settings())

        first, second = await asyncio.gather(load_once(), load_once())
        assert first.group_chat_id == second.group_chat_id == TEST_CHAT_ID
        assert first.alpha_user_id == second.alpha_user_id == TEST_ALPHA_USER_ID
        async with sessions() as session:
            count = await session.scalar(select(func.count()).select_from(AQUBInstallationBinding))
            assert count == 1
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_non_ping_receipt_is_not_binding_evidence() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB installation binding test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await _insert_receipt(
                session,
                update_id=905,
                payload=_ping_payload(text_value="обычное сообщение"),
                processed_at=TEST_READY_AT,
            )
            with pytest.raises(ValueError, match="evidence is unavailable"):
                await load_aqub_production_binding(session, _settings())
    finally:
        await _drop_schema(schema, admin_engine, engine)
