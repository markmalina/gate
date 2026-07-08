"""Telegram webhook tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.main import app
from app.models.domain import AuditLog, Bot, GateEvent
from app.services.telegram import TelegramClient


class FakeScalarResult:
    """Minimal SQLAlchemy scalar result fake."""

    def __init__(self, bot: Bot | None) -> None:
        self._bot = bot

    def scalar_one_or_none(self) -> Bot | None:
        return self._bot


class FakeSession:
    """Minimal async session fake for webhook persistence assertions."""

    def __init__(self) -> None:
        self.objects: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> FakeScalarResult:
        del statement
        bots = [item for item in self.objects if isinstance(item, Bot)]
        bot = bots[0] if bots else None
        return FakeScalarResult(bot)

    def add(self, instance: object) -> None:
        self.objects.append(instance)

    async def flush(self) -> None:
        for item in self.objects:
            has_no_id = getattr(item, "id", None) is None
            if isinstance(item, Bot | GateEvent | AuditLog) and has_no_id:
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
        return {"ok": True}


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


def message_update(text: str) -> dict[str, Any]:
    return {
        "update_id": 100,
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
    assert telegram_client.calls == []
    assert session.commits == 1


@pytest.mark.anyio
async def test_ping_triggers_one_telegram_send_and_persists_event_and_audit(
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
    assert telegram_client.calls == [
        {"chat_id": "222", "text": "Gate online", "business_connection_id": None}
    ]

    events = [item for item in session.objects if isinstance(item, GateEvent)]
    assert len(events) == 1
    assert events[0].text == " ping "
    assert events[0].event_type == "message"

    audits = [item for item in session.objects if isinstance(item, AuditLog)]
    assert [audit.action for audit in audits] == [
        "telegram_update_received",
        "auto_safe_reply_sent",
    ]


@pytest.mark.anyio
async def test_business_message_passes_business_connection_id(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    _, telegram_client = webhook_context
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
    assert len(events) == 1
    assert events[0].event_type == "business_connection"
    assert events[0].chat_id == "555"
