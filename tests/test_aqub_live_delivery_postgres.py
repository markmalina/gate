"""PostgreSQL proof for AQUB live Telegram delivery settlement."""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID, uuid4

import pytest
from app.core.config import get_settings
from app.infrastructure.aqub_live import (
    AQUBLiveDeliveryError,
    AQUBLiveDeliveryService,
    ParsedAQUBDelivery,
)
from app.models.aqub_live import AQUBLiveDelivery
from app.services.telegram import TelegramApiError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_CHAT_ID = "-1001234567890"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.message_id = 7001

    async def send_plain_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, object]:
        del business_connection_id, reply_to_message_id
        self.calls.append((chat_id, text))
        return {"ok": True, "result": {"message_id": self.message_id}}


class RetryOnceTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def send_plain_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, object]:
        del business_connection_id, reply_to_message_id
        self.calls.append((chat_id, text))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TelegramApiError(
                error_code=429,
                description="Too Many Requests",
                http_status=429,
            )
        return {"ok": True, "result": {"message_id": self.message_id}}


class UnknownOnceTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def send_plain_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, object]:
        del business_connection_id, reply_to_message_id
        self.calls.append((chat_id, text))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("transport outcome unknown")
        return {"ok": True, "result": {"message_id": self.message_id}}


def _opaque(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(signing_key, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


def _command(
    signing_key: bytes,
    *,
    command_id: UUID | None = None,
    key: str = "assignment:test:1",
    text_value: str = "Exercise",
) -> ParsedAQUBDelivery:
    command_id = command_id or uuid4()
    raw = f"{command_id}|{key}|{text_value}".encode()
    return ParsedAQUBDelivery(
        command_id=command_id,
        idempotency_key=key,
        chat_ref=_opaque(signing_key, "chat", TEST_CHAT_ID),
        assignment_ref="assignment:opaque",
        step_ref="step:opaque",
        text=text_value,
        request_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _service(session, telegram, signing_key: bytes) -> AQUBLiveDeliveryService:
    return AQUBLiveDeliveryService(
        session=session,
        telegram=telegram,
        signing_key=signing_key,
        raw_chat_id=TEST_CHAT_ID,
    )


async def _schema_engine(database_url: str, prefix: str):
    schema = f"{prefix}_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(AQUBLiveDelivery.__table__.create)
    return schema, admin_engine, engine


async def _drop_schema(schema: str, admin_engine, engine) -> None:
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


@pytest.mark.anyio
async def test_live_delivery_sends_once_and_exact_replay_returns_same_receipts() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema, admin_engine, engine = await _schema_engine(database_url, "aqub_live_delivery")
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = FakeTelegram()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        command = _command(signing_key)

        async with sessions() as session:
            first = await _service(session, telegram, signing_key).deliver(command)
        async with sessions() as session:
            duplicate = await _service(session, telegram, signing_key).deliver(command)

        assert first["ack"]["status"] == "committed"
        assert first["terminal"]["status"] == "sent"
        assert first["terminal"]["delivered_message_ref"] == _opaque(
            signing_key,
            "message",
            str(telegram.message_id),
        )
        assert duplicate["ack"]["status"] == "duplicate"
        assert (
            duplicate["ack"]["command_receipt_ref"]
            == first["ack"]["command_receipt_ref"]
        )
        assert duplicate["terminal"] == first["terminal"]
        assert telegram.calls == [(TEST_CHAT_ID, command.text)]
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_definitive_telegram_failure_retries_exact_command_and_then_sends() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema, admin_engine, engine = await _schema_engine(database_url, "aqub_live_retry")
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = RetryOnceTelegram()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        command = _command(signing_key, key="retryable-key")

        async with sessions() as session:
            failed = await _service(session, telegram, signing_key).deliver(command)
        async with sessions() as session:
            retried = await _service(session, telegram, signing_key).deliver(command)

        assert failed["ack"]["status"] == "committed"
        assert failed["terminal"]["status"] == "failed"
        assert failed["terminal"]["reason"] == "telegram_429"
        assert retried["ack"]["status"] == "duplicate"
        assert retried["terminal"]["status"] == "sent"
        assert len(telegram.calls) == 2
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_unknown_send_outcome_is_not_retried_on_exact_replay() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema, admin_engine, engine = await _schema_engine(database_url, "aqub_live_unknown")
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = UnknownOnceTelegram()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        command = _command(signing_key, key="unknown-key")

        async with sessions() as session:
            first = await _service(session, telegram, signing_key).deliver(command)
        async with sessions() as session:
            duplicate = await _service(session, telegram, signing_key).deliver(command)

        assert first["terminal"]["status"] == "failed"
        assert first["terminal"]["reason"] == "send_outcome_unknown"
        assert duplicate["ack"]["status"] == "duplicate"
        assert duplicate["terminal"] == first["terminal"]
        assert len(telegram.calls) == 1
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_live_delivery_mutated_replay_fails_closed_without_second_send() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB live delivery test")

    schema, admin_engine, engine = await _schema_engine(database_url, "aqub_live_mutated")
    signing_key = b"0123456789abcdef0123456789abcdef-delivery"
    telegram = FakeTelegram()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        original = _command(signing_key, key="stable-key")
        mutated = _command(signing_key, key="stable-key", text_value="Changed")

        async with sessions() as session:
            await _service(session, telegram, signing_key).deliver(original)
        async with sessions() as session:
            with pytest.raises(AQUBLiveDeliveryError) as raised:
                await _service(session, telegram, signing_key).deliver(mutated)

        assert raised.value.code == "aqub_delivery_mutated_replay"
        assert raised.value.status_code == 409
        assert len(telegram.calls) == 1
    finally:
        await _drop_schema(schema, admin_engine, engine)
