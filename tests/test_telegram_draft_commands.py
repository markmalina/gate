"""Telegram draft command tests."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.api.routes import (
    TelegramAdminCommand,
    _build_reply_draft_text,
    _create_reply_draft,
    _parse_admin_console_command,
)
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
from sqlalchemy.ext.asyncio import AsyncSession

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

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Minimal async session fake for draft command unit assertions."""

    def __init__(self) -> None:
        self.objects: list[object] = []
        self.inbox_rows: list[tuple[ReplyTask, Conversation, Message]] = []

    async def execute(self, statement: object) -> FakeScalarResult:
        statement_text = str(statement)
        if "JOIN conversations" in statement_text:
            return FakeScalarResult(rows=self.inbox_rows)
        return FakeScalarResult()

    def add(self, instance: object) -> None:
        self.objects.append(instance)

    async def flush(self) -> None:
        for item in self.objects:
            if isinstance(item, MODEL_TYPES) and item.id is None:
                item.id = uuid.uuid4()


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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_draft_command_parser_accepts_task_reference() -> None:
    command = _parse_admin_console_command("/draft abc12345")

    assert command == TelegramAdminCommand(name="draft", task_ref="abc12345")


def test_reply_draft_text_is_bounded_and_not_a_send_action() -> None:
    draft = _build_reply_draft_text("hello" * 200)

    assert draft.startswith("Привет.")
    assert "Вернусь с ответом чуть позже." in draft
    assert len(draft) < 500


@pytest.mark.anyio
async def test_create_reply_draft_stores_draft_without_closing_task() -> None:
    fake_session = FakeSession()
    task, _conversation, _message = add_reply_task_context(fake_session)
    task_ref = str(task.id)[:8]
    command = TelegramAdminCommand(name="draft", task_ref=task_ref)

    result = await _create_reply_draft(
        command=command,
        session=cast(AsyncSession, fake_session),
    )

    assert "Draft created:" in result
    assert f"task: {task_ref}" in result
    assert "please reply today" in result
    assert "Not sent." in result

    drafts = [item for item in fake_session.objects if isinstance(item, ReplyDraft)]
    assert len(drafts) == 1
    assert drafts[0].reply_task_id == task.id
    assert drafts[0].status == ReplyDraftStatus.GENERATED.value
    assert drafts[0].source == "gate-local-draft-v1"
    assert "please reply today" in drafts[0].draft_text
    assert drafts[0].prompt_context["bounded_context"] is True
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value

    audit_actions = [item.action for item in fake_session.objects if isinstance(item, AuditLog)]
    assert "reply_draft_created_from_telegram" in audit_actions
