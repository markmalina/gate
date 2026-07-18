"""Telegram webhook tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
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
    ReplyTask,
    ReplyTaskStatus,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient
from httpx import ASGITransport, AsyncClient

MODEL_TYPES = (Bot, GateEvent, AuditLog, TelegramUpdate, Message, Conversation, ReplyTask)


class FakeReceiptStore:
    """Durably accept test envelopes without coupling legacy tests to SQLAlchemy."""

    def __init__(self) -> None:
        self.update_ids: set[int] = set()

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        duplicate = command.update_id in self.update_ids
        self.update_ids.add(command.update_id)
        return TelegramReceiptAcceptance(
            receipt_id=f"receipt-{command.update_id}",
            outbox_event_id=f"outbox-{command.update_id}",
            duplicate=duplicate,
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

    def one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._scalar

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Minimal async session fake for webhook persistence assertions."""

    def __init__(self) -> None:
        self.objects: list[object] = []
        self.inbox_rows: list[tuple[ReplyTask, Conversation, Message]] = []
        self.scalar_results: list[Any] = []
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
        if "JOIN conversations" in statement_text:
            return FakeScalarResult(rows=self.inbox_rows)
        if "FROM conversations" in statement_text:
            conversations = [item for item in self.objects if isinstance(item, Conversation)]
            return FakeScalarResult(conversations[0] if conversations else None)
        if "FROM reply_tasks" in statement_text and "count" not in statement_text:
            tasks = [item for item in self.objects if isinstance(item, ReplyTask)]
            return FakeScalarResult(rows=tasks)
        if self.scalar_results:
            return FakeScalarResult(scalar=self.scalar_results.pop(0))
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

    def __init__(self) -> None:
        pass

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
    monkeypatch.delenv("TELEGRAM_ADMIN_USER_IDS", raising=False)
    get_settings.cache_clear()

    session = FakeSession()
    telegram_client = FakeTelegramClient()
    receipt_store = FakeReceiptStore()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield session

    def override_telegram_client() -> TelegramClient:
        return telegram_client

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = override_telegram_client
    app.dependency_overrides[get_telegram_receipt_store] = lambda: receipt_store

    yield session, telegram_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def message_update(text: str, update_id: int = 100, user_id: int = 111) -> dict[str, Any]:
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
    text: str = "please reply",
    task_status: str = ReplyTaskStatus.NEEDS_REPLY.value,
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
        status=task_status,
        priority=100,
        reason="inbound_human_text",
        created_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
        updated_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
    )
    session.objects.extend([conversation, message, task])
    session.inbox_rows.append((task, conversation, message))
    return task, conversation, message


def clear_telegram_update_receipts(session: FakeSession) -> None:
    """Clear fake idempotency receipts between distinct webhook updates in one test."""

    session.objects = [
        item for item in session.objects if not isinstance(item, TelegramUpdate)
    ]


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
    monkeypatch.delenv("TELEGRAM_ADMIN_USER_IDS", raising=False)
    get_settings.cache_clear()
    session = FakeSession()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield session

    def override_telegram_client() -> TelegramClient:
        return FailingTelegramClient()

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID", "bc-1")
    get_settings.cache_clear()
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID", "connection-1")
    get_settings.cache_clear()
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


@pytest.mark.anyio
async def test_inbound_non_ping_creates_conversation_and_needs_reply_task(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, _telegram_client = webhook_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("where are we?", update_id=400),
        )

    assert response.status_code == 200
    conversations = [item for item in session.objects if isinstance(item, Conversation)]
    tasks = [item for item in session.objects if isinstance(item, ReplyTask)]
    assert len(conversations) == 1
    assert conversations[0].chat_id == "222"
    assert conversations[0].status == ConversationStatus.OPEN.value
    assert conversations[0].requires_reply is True
    assert len(tasks) == 1
    assert tasks[0].status == ReplyTaskStatus.NEEDS_REPLY.value


@pytest.mark.anyio
async def test_duplicate_update_does_not_create_duplicate_reply_task(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, _telegram_client = webhook_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("hello", update_id=401),
        )
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("hello", update_id=401),
        )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert len([item for item in session.objects if isinstance(item, ReplyTask)]) == 1


@pytest.mark.anyio
async def test_ping_auto_reply_satisfies_unanswered_task(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
) -> None:
    session, _telegram_client = webhook_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("ping", update_id=402),
        )

    assert response.status_code == 200
    conversations = [item for item in session.objects if isinstance(item, Conversation)]
    tasks = [item for item in session.objects if isinstance(item, ReplyTask)]
    assert conversations[0].status == ConversationStatus.REPLIED.value
    assert conversations[0].requires_reply is False
    assert tasks[0].status == ReplyTaskStatus.REPLIED.value


@pytest.mark.anyio
async def test_telegram_admin_status_command_replies_with_status(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
    monkeypatch.setenv("GATE_ENV", "development")
    get_settings.cache_clear()
    session.scalar_results.extend([12, None, 18, None, 4, None, 2, 0])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("/status", update_id=500),
        )

    assert response.status_code == 200
    assert response.json()["send_status"] == "sent"
    assert telegram_client.calls[-1]["text"].splitlines() == [
        "Gate status",
        "Environment: development",
        "Commit: unknown",
        "Bot: gate_m_bot",
        "Messages: 4",
        "Reply tasks: 2",
        "Unanswered: 0",
    ]
    assert len([item for item in session.objects if isinstance(item, ReplyTask)]) == 0


@pytest.mark.anyio
async def test_telegram_admin_inbox_command_replies_with_unanswered_messages(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
    task, _conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("где я не ответил", update_id=501),
        )

    assert response.status_code == 200
    reply_text = telegram_client.calls[-1]["text"]
    assert "Unanswered inbox:" in reply_text
    assert "please reply" in reply_text
    assert f"actions: /draft_{task_ref}" in reply_text
    assert f"/done_{task_ref}" in reply_text
    assert "payload" not in reply_text


@pytest.mark.anyio
async def test_non_admin_inbox_command_does_not_expose_admin_data(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "999")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update("/inbox", update_id=502),
        )

    assert response.status_code == 200
    assert telegram_client.calls[-1]["text"] == "Gate online"
    assert "Unanswered" not in telegram_client.calls[-1]["text"]
    assert len([item for item in session.objects if isinstance(item, ReplyTask)]) == 0


@pytest.mark.anyio
async def test_telegram_admin_done_command_marks_task_replied(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
    task, conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/done {task_ref}", update_id=503),
        )

    assert response.status_code == 200
    assert telegram_client.calls[-1]["text"] == f"Marked replied: {task_ref}"
    assert task.status == ReplyTaskStatus.REPLIED.value
    assert conversation.status == ConversationStatus.REPLIED.value
    assert conversation.requires_reply is False
    assert len([item for item in session.objects if isinstance(item, ReplyTask)]) == 1
    audit_actions = [item.action for item in session.objects if isinstance(item, AuditLog)]
    assert "reply_task_done_from_telegram" in audit_actions


@pytest.mark.anyio
async def test_telegram_admin_dismiss_snooze_and_reopen_commands_change_task_state(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "111")
    task, conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dismiss_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/dismiss {task_ref}", update_id=504),
        )
        clear_telegram_update_receipts(session)
        snooze_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/snooze {task_ref}", update_id=505),
        )
        clear_telegram_update_receipts(session)
        reopen_response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/reopen {task_ref}", update_id=506),
        )

    assert dismiss_response.status_code == 200
    assert snooze_response.status_code == 200
    assert reopen_response.status_code == 200
    assert telegram_client.calls[-3]["text"] == f"Dismissed: {task_ref}"
    assert telegram_client.calls[-2]["text"] == f"Snoozed: {task_ref}"
    assert telegram_client.calls[-1]["text"] == f"Reopened: {task_ref}"
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
    assert conversation.status == ConversationStatus.OPEN.value
    assert conversation.requires_reply is True

    audit_actions = [item.action for item in session.objects if isinstance(item, AuditLog)]
    assert "reply_task_dismiss_from_telegram" in audit_actions
    assert "reply_task_snooze_from_telegram" in audit_actions
    assert "reply_task_reopen_from_telegram" in audit_actions


@pytest.mark.anyio
async def test_non_admin_done_command_does_not_change_task_state(
    webhook_context: tuple[FakeSession, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, telegram_client = webhook_context
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "999")
    task, conversation, _message = add_reply_task_context(session)
    task_ref = str(task.id)[:8]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=message_update(f"/done {task_ref}", update_id=507),
        )

    assert response.status_code == 200
    assert telegram_client.calls[-1]["text"] == "Gate online"
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
    assert conversation.requires_reply is True
    audit_actions = [item.action for item in session.objects if isinstance(item, AuditLog)]
    assert "reply_task_done_from_telegram" not in audit_actions
