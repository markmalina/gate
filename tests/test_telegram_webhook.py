"""Telegram webhook tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.main import app
from app.models.domain import AuditLog, Bot, GateEvent, Message, MessageDirection, TelegramUpdate
from app.services.telegram import TelegramClient
from httpx import ASGITransport, AsyncClient


class FakeScalarResult:
    """Minimal SQLAlchemy scalar result fake."""

    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class FakeSession:
    """Minimal async session fake for webhook persistence assertions."""

    def __init__(self) -> None:
        self.objects: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> FakeScalarResult:
        statement_text = str(statement)
        if "FROM bots" in statement_text:
            bots = [item for item in self.objects if isinstance(item, Bot)]
            return FakeScalarResult(bots[0] if bots else None)
        if "FROM telegram_updates" in statement_text:
            updates = [item for item in self.objects if isinstance(item, TelegramUpdate)]
            return FakeScalarResult(updates[0] if updates else None)
        return FakeScalarResult(None)

    def add(self, instance: object) -> None:
        self.objects.append(instance)

    async def flush(self) -> None:
        for item in self.objects:
            if isinstance(item, Bot | GateEvent | AuditLog | TelegramUpdate | Message):
                if item.id is None:
                    item.id = uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeTelegramClient(TelegramClient):
    """Telegram client fake collecting outbound messages."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "business_connection_id": business_connection_id,
            }
        )
        return {"ok": True, "result": {"message_id": 999}}


class FailingTelegramClient(TelegramClient):
    """Telegram client fake that always fails sending."""

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
    ) -> dict[str, Any]:
        del chat_id, text, business_connection_id
        raise RuntimeError("telegram unavailable")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def webhook_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FakeSession, FakeTelegramClient]]:
    monkeypatch.setenv("TELEGRAM_" + "WEBHOOK_SECRET", "test-secret")
    get_settings.cache_clear()

    session = FakeSession()
    telegram_client = FakeTelegramClient()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield session

    def override_telegram_client() -> TelegramClient:
        return telegram_client

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = override_telegram_client

    yield session, telegram_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def message_update(text: str, update_id: int = 100) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": 111},
            "chat": {"id": 222, "type": "private"},
            "date": 1,
            "text": text,
        },
    }


@pytest.mark.anyio
async def test_telegram_webhook_rejects_invalid_secret(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json=message_update("ping"),
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_telegram_webhook_accepts_valid_secret_without_auto_reply(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("hello"),
        )

    assert response.status_code == 200
    assert response.json()["auto_reply"] is None
    assert response.json()["send_status"] == "not_applicable"
    assert telegram_client.calls == []
    assert session.commits == 1

    updates = [item for item in session.objects if isinstance(item, TelegramUpdate)]
    messages = [item for item in session.objects if isinstance(item, Message)]
    assert len(updates) == 1
    assert updates[0].update_id == 100
    assert len(messages) == 1
    assert messages[0].direction == MessageDirection.INBOUND.value
    assert messages[0].telegram_update_id == 100
    assert messages[0].telegram_message_id == 10


@pytest.mark.anyio
async def test_ping_triggers_one_telegram_send_and_persists_event_message_and_audit(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(" ping "),
        )

    assert response.status_code == 200
    assert response.json()["auto_reply"] == "Gate online"
    assert response.json()["send_status"] == "sent"
    assert telegram_client.calls == [
        {"chat_id": "222", "text": "Gate online", "business_connection_id": None}
    ]
    assert session.commits == 2

    events = [item for item in session.objects if isinstance(item, GateEvent)]
    assert len(events) == 1
    assert events[0].text == " ping "
    assert events[0].event_type == "message"
    assert events[0].telegram_update_id == 100
    assert events[0].telegram_message_id == 10

    messages = [item for item in session.objects if isinstance(item, Message)]
    assert [message.direction for message in messages] == [
        MessageDirection.INBOUND.value,
        MessageDirection.OUTBOUND.value,
    ]
    assert messages[0].text == " ping "
    assert messages[1].text == "Gate online"

    audits = [item for item in session.objects if isinstance(item, AuditLog)]
    assert [audit.action for audit in audits] == [
        "telegram_update_received",
        "auto_safe_reply_sent",
    ]


@pytest.mark.anyio
async def test_duplicate_telegram_update_does_not_duplicate_storage_or_send(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("ping", update_id=200),
        )
        second_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("ping", update_id=200),
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["duplicate"] is True
    assert second_response.json()["send_status"] == "duplicate"

    assert len([item for item in session.objects if isinstance(item, TelegramUpdate)]) == 1
    assert len([item for item in session.objects if isinstance(item, GateEvent)]) == 1
    assert len([item for item in session.objects if isinstance(item, Message)]) == 2
    assert len(telegram_client.calls) == 1


@pytest.mark.anyio
async def test_inbound_message_is_saved_when_auto_reply_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_" + "WEBHOOK_SECRET", "test-secret")
    get_settings.cache_clear()
    session = FakeSession()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield session

    def override_telegram_client() -> TelegramClient:
        return FailingTelegramClient("token")

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = override_telegram_client

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
                json=message_update("ping", update_id=300),
            )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["send_status"] == "failed"
    assert session.commits == 2
    assert len([item for item in session.objects if isinstance(item, TelegramUpdate)]) == 1
    assert len([item for item in session.objects if isinstance(item, GateEvent)]) == 1

    messages = [item for item in session.objects if isinstance(item, Message)]
    assert len(messages) == 1
    assert messages[0].direction == MessageDirection.INBOUND.value

    audits = [item for item in session.objects if isinstance(item, AuditLog)]
    assert [audit.action for audit in audits] == [
        "telegram_update_received",
        "auto_safe_reply_failed",
    ]


@pytest.mark.anyio
async def test_business_message_passes_business_connection_id(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context
    update = {
        "update_id": 101,
        "business_message": {
            "message_id": 11,
            "business_connection_id": "bc-1",
            "from": {"id": 111},
            "chat": {"id": 333, "type": "private"},
            "date": 1,
            "text": "ping",
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=update,
        )

    assert response.status_code == 200
    assert telegram_client.calls == [
        {"chat_id": "333", "text": "Gate online", "business_connection_id": "bc-1"}
    ]

    events = [item for item in session.objects if isinstance(item, GateEvent)]
    messages = [item for item in session.objects if isinstance(item, Message)]
    assert events[0].business_connection_id == "bc-1"
    assert messages[0].business_connection_id == "bc-1"
    assert messages[1].business_connection_id == "bc-1"


@pytest.mark.anyio
async def test_business_connection_update_is_persisted_without_auto_reply(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context
    update = {
        "update_id": 102,
        "business_connection": {
            "id": "connection-1",
            "user": {"id": 444},
            "user_chat_id": 555,
            "date": 1,
            "can_reply": True,
            "is_enabled": True,
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=update,
        )

    assert response.status_code == 200
    assert telegram_client.calls == []

    events = [item for item in session.objects if isinstance(item, GateEvent)]
    messages = [item for item in session.objects if isinstance(item, Message)]
    assert len(events) == 1
    assert events[0].event_type == "business_connection"
    assert events[0].chat_id == "555"
    assert len(messages) == 1
    assert messages[0].event_type == "business_connection"
    assert messages[0].text is None
