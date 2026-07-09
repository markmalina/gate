"""Telegram /test guide edit flow tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.main import app
from app.models.domain import (
    AuditLog,
    Bot,
    Conversation,
    GateEvent,
    Message,
    MessageDirection,
    ReplyTask,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient
from httpx import ASGITransport, AsyncClient

MODEL_TYPES = (Bot, GateEvent, AuditLog, TelegramUpdate, Message, Conversation, ReplyTask)


class FakeScalars:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeScalarResult:
    def __init__(
        self,
        row: object | None = None,
        rows: list[Any] | None = None,
        scalar: Any | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or ([] if row is None else [row])
        self._scalar = scalar

    def scalar_one_or_none(self) -> object | None:
        return self._row

    def scalar_one(self) -> Any:
        return self._scalar

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self) -> None:
        self.objects: list[object] = []
        self.commits = 0

    async def execute(self, statement: object) -> FakeScalarResult:
        statement_text = str(statement)
        if "FROM bots" in statement_text:
            bots = [item for item in self.objects if isinstance(item, Bot)]
            return FakeScalarResult(bots[0] if bots else None)
        if "FROM telegram_updates" in statement_text:
            updates = [item for item in self.objects if isinstance(item, TelegramUpdate)]
            return FakeScalarResult(updates[0] if updates else None)
        if "FROM conversations" in statement_text:
            conversations = [item for item in self.objects if isinstance(item, Conversation)]
            return FakeScalarResult(conversations[0] if conversations else None)
        if "FROM messages" in statement_text and "count" not in statement_text:
            messages = [
                item
                for item in self.objects
                if isinstance(item, Message)
                and item.direction == MessageDirection.OUTBOUND.value
                and (item.text or "").startswith("Gate test: draft flow")
            ]
            return FakeScalarResult(messages[-1] if messages else None)
        return FakeScalarResult(None)

    def add(self, instance: object) -> None:
        self.objects.append(instance)

    async def flush(self) -> None:
        for item in self.objects:
            if isinstance(item, MODEL_TYPES) and item.id is None:
                item.id = uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class FakeTelegramClient(TelegramClient):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

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

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        business_connection_id: str | None = None,
    ) -> dict[str, Any]:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "business_connection_id": business_connection_id,
            }
        )
        return {"ok": True, "result": {"message_id": message_id, "text": text}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def webhook_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FakeSession, FakeTelegramClient]]:
    monkeypatch.setenv("TELEGRAM_" + "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
    monkeypatch.setenv(
        "RAILWAY_GIT_COMMIT_SHA",
        "68a2e4f150f47fa30e8d2968ef665e9ce335a121",
    )
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


def message_update(text: str, update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 10,
            "from": {"id": 111},
            "chat": {"id": 222, "type": "private"},
            "date": 1,
            "text": text,
        },
    }


def clear_telegram_update_receipts(session: FakeSession) -> None:
    session.objects = [
        item for item in session.objects if not isinstance(item, TelegramUpdate)
    ]


@pytest.mark.anyio
async def test_test_probe_edits_original_test_guide_with_real_task_ref(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        test_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("/test", update_id=700),
        )
        clear_telegram_update_receipts(session)
        probe_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("test guided draft", update_id=701),
        )

    assert test_response.status_code == 200
    assert probe_response.status_code == 200
    assert "Task: <pending>" in telegram_client.calls[-1]["text"]

    tasks = [item for item in session.objects if isinstance(item, ReplyTask)]
    assert len(tasks) == 1
    task_ref = str(tasks[0].id)[:8]

    assert len(telegram_client.edits) == 1
    edited_text = telegram_client.edits[0]["text"]
    assert telegram_client.edits[0]["message_id"] == 999
    assert f"Task: {task_ref}" in edited_text
    assert f"/draft_{task_ref}" in edited_text
    assert f"/drafts_{task_ref}" in edited_text
    assert f"/done_{task_ref}" in edited_text
    assert "<task>" not in edited_text
    assert "<pending>" not in edited_text

    audit_actions = [item.action for item in session.objects if isinstance(item, AuditLog)]
    assert "telegram_test_guide_updated" in audit_actions
