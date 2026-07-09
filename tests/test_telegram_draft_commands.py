"""Telegram draft command tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
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
    ConversationStatus,
    GateEvent,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient
from httpx import ASGITransport, AsyncClient

MODEL_TYPES = (
    Bot,
    GateEvent,
    AuditLog,
    TelegramUpdate,
    Message,
    Conversation,
    ReplyTask,
    ReplyDraft,
)


class FakeScalars:
    """Minimal scalar collection fake."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeScalarResult:
    """Minimal SQLAlchemy scalar result fake."""

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
    """Minimal async session fake for draft command assertions."""

    def __init__(self) -> None:
        self.objects: list[object] = []
        self.inbox_rows: list[tuple[ReplyTask, Conversation, Message]] = []
        self.commits = 0

    async def execute(self, statement: object) -> FakeScalarResult:
        statement_text = str(statement)
        if "FROM bots" in statement_text:
            bots = [item for item in self.objects if isinstance(item, Bot)]
            return FakeScalarResult(bots[0] if bots else None)
        if "FROM telegram_updates" in statement_text:
            updates = [item for item in self.objects if isinstance(item, TelegramUpdate)]
            return FakeScalarResult(updates[0] if updates else None)
        if "JOIN conversations" in statement_text:
            return FakeScalarResult(rows=self.inbox_rows)
        if "FROM conversations" in statement_text:
            conversations = [item for item in self.objects if isinstance(item, Conversation)]
            return FakeScalarResult(conversations[0] if conversations else None)
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def webhook_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FakeSession, FakeTelegramClient]]:
    monkeypatch.setenv("TELEGRAM_" + "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
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


def message_update(text: str, update_id: int = 700, user_id: int = 111) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": user_id},
            "chat": {"id": 222, "type": "private"},
            "date": 1,
            "text": text,
        },
    }


def add_reply_task_context(
    session: FakeSession,
    *,
    text: str = "please reply today",
) -> tuple[ReplyTask, Conversation, Message]:
    bot_id = uuid.uuid4()
    conversation = Conversation(
        id=uuid.uuid4(),
        bot_id=bot_id,
        chat_id="222",
        status=ConversationStatus.OPEN.value,
        requires_reply=True,
        last_message_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
        last_inbound_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
    )
    message = Message(
        id=uuid.uuid4(),
        bot_id=bot_id,
        direction=MessageDirection.INBOUND.value,
        source="telegram",
        event_type="message",
        chat_id="222",
        sender_id="111",
        text=text,
        payload={},
        received_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
        created_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
    )
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        status=ReplyTaskStatus.NEEDS_REPLY.value,
        priority=100,
        reason="inbound_human_text",
        created_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
        updated_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
    )
    session.objects.extend([conversation, message, task])
    session.inbox_rows.append((task, conversation, message))
    return task, conversation, message


@pytest.mark.anyio
async def test_telegram_admin_draft_command_creates_stored_draft_without_sending_to_client(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, telegram_client = webhook_context
    task, _conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/draft {task_ref}"),
        )

    assert response.status_code == 200
    assert response.json()["send_status"] == "sent"
    reply_text = telegram_client.calls[-1]["text"]
    assert "Draft created:" in reply_text
    assert f"task: {task_ref}" in reply_text
    assert "please reply today" in reply_text
    assert "Not sent." in reply_text

    drafts = [item for item in session.objects if isinstance(item, ReplyDraft)]
    assert len(drafts) == 1
    assert drafts[0].reply_task_id == task.id
    assert drafts[0].status == ReplyDraftStatus.GENERATED.value
    assert drafts[0].source == "gate-local-draft-v1"
    assert "please reply today" in drafts[0].draft_text
    assert drafts[0].prompt_context["bounded_context"] is True
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value

    audit_actions = [item.action for item in session.objects if isinstance(item, AuditLog)]
    assert "reply_draft_created_from_telegram" in audit_actions
    assert "telegram_admin_console_draft_sent" in audit_actions


@pytest.mark.anyio
async def test_non_admin_draft_command_does_not_create_draft(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "999")
    task, _conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/draft {task_ref}"),
        )

    assert response.status_code == 200
    assert telegram_client.calls[-1]["text"] == "Gate online"
    assert len([item for item in session.objects if isinstance(item, ReplyDraft)]) == 0
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
