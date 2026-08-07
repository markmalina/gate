"""PostgreSQL proof for AQUB live Telegram delivery settlement."""

from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.infrastructure.aqub_live import (
    AQUBLiveDeliveryError,
    AQUBLiveDeliveryService,
    ParsedAQUBDelivery,
)
from app.infrastructure.aqub_s0_preflight import AQUB_RELEASE_GROUP_CHAT_ID
from app.models.aqub_live import AQUBLiveDelivery
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.message_id = 7001

    async def send_plain_message(self, *, chat_id: str, text: str, business_connection_id=None):
        self.calls.append((chat_id, text))
        return {"ok": True, "result": {"message_id": self.message_id}}


def _opaque(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(signing_key, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


def _command(signing_key: bytes, *, command_id=None, key: str = "assignment:test:1", text_value: str = "Exercise"):
    command_id = command_id or uuid4()
    raw = f"{command_id}|{key}|{text_value}".encode()
    return ParsedAQUBDelivery(
        command_id=command_id,
        idempotency_key=key,
        chat_ref=_opaque(signing_key, "chat", AQUB_RELEASE_GROUP_CHAT_ID),
        assignment_ref="assignment:opaque",
        step_ref="step:opaque",
        text=text_value,
        request_sha256=hashlib.sha256(raw).hexdigest(),
    )


@pytest.mark.anyio
async def test_live_delivery_sends_once_and_exact_replay_returns_same_receipts() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema = f"aqub_live_delivery_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = FakeTelegram()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AQUBLiveDelivery.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        command = _command(signing_key)

        async with sessions() as session:
            first = await AQUBLiveDeliveryService(
                session=session,
                telegram=telegram,
                signing_key=signing_key,
            ).deliver(command)
        async with sessions() as session:
            duplicate = await AQUBLiveDeliveryService(
                session=session,
                telegram=telegram,
                signing_key=signing_key,
            ).deliver(command)

        assert first["ack"]["status"] == "committed"
        assert first["terminal"]["status"] == "sent"
        assert first["terminal"]["delivered_message_ref"] == _opaque(
            signing_key, "message", str(telegram.message_id)
        )
        assert duplicate["ack"]["status"] == "duplicate"
        assert duplicate["ack"]["command_receipt_ref"] == first["ack"]["command_receipt_ref"]
        assert duplicate["terminal"] == first["terminal"]
        assert telegram.calls == [(AQUB_RELEASE_GROUP_CHAT_ID, command.text)]
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_live_delivery_mutated_replay_fails_closed_without_second_send() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema = f"aqub_live_mutated_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = FakeTelegram()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AQUBLiveDelivery.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        original = _command(signing_key, key="stable-key")
        mutated = _command(signing_key, key="stable-key", text_value="Changed")

        async with sessions() as session:
            await AQUBLiveDeliveryService(
                session=session,
                telegram=telegram,
                signing_key=signing_key,
            ).deliver(original)
        async with sessions() as session:
            with pytest.raises(AQUBLiveDeliveryError) as raised:
                await AQUBLiveDeliveryService(
                    session=session,
                    telegram=telegram,
                    signing_key=signing_key,
                ).deliver(mutated)

        assert raised.value.code == "aqub_delivery_mutated_replay"
        assert raised.value.status_code == 409
        assert len(telegram.calls) == 1
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
