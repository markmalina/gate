"""Telegram draft command tests."""

import uuid
from datetime import UTC, datetime

from app.api.routes import (
    TelegramAdminCommand,
    _build_reply_draft_text,
    _format_reply_draft_result,
    _format_telegram_test_guide,
    _parse_admin_console_command,
)
from app.models.domain import (
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
)


def test_draft_command_parser_accepts_space_task_reference() -> None:
    command = _parse_admin_console_command("/draft abc12345")

    assert command == TelegramAdminCommand(name="draft", task_ref="abc12345")


def test_draft_command_parser_accepts_clickable_underscore_task_reference() -> None:
    command = _parse_admin_console_command("/draft_abc12345")

    assert command == TelegramAdminCommand(name="draft", task_ref="abc12345")


def test_action_command_parser_accepts_clickable_underscore_task_reference() -> None:
    assert _parse_admin_console_command("/done_abc12345") == TelegramAdminCommand(
        name="done", task_ref="abc12345"
    )
    assert _parse_admin_console_command("/dismiss_abc12345") == TelegramAdminCommand(
        name="dismiss", task_ref="abc12345"
    )
    assert _parse_admin_console_command("/snooze_abc12345") == TelegramAdminCommand(
        name="snooze", task_ref="abc12345"
    )


def test_test_command_parser_accepts_test_command() -> None:
    command = _parse_admin_console_command("/test")

    assert command == TelegramAdminCommand(name="test")


def test_test_guide_contains_clickable_flow_and_expected_results() -> None:
    guide = _format_telegram_test_guide()

    assert "Gate test: draft flow" in guide
    assert "test guided draft" in guide
    assert "Press /inbox" in guide
    assert "Press /draft_<task> from /inbox" in guide
    assert "Press /done_<task>" in guide
    assert "No unanswered messages." in guide


def test_reply_draft_text_is_bounded_and_not_a_send_action() -> None:
    draft = _build_reply_draft_text("hello" * 200)

    assert draft.startswith("Привет.")
    assert "Вернусь с ответом чуть позже." in draft
    assert len(draft) < 500


def test_format_reply_draft_result_shows_not_sent() -> None:
    message_id = uuid.uuid4()
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        trigger_message_id=message_id,
        status=ReplyTaskStatus.NEEDS_REPLY.value,
        priority=100,
        reason="inbound_human_text",
        created_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
        updated_at=datetime(2026, 7, 9, 11, 41, tzinfo=UTC),
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        reply_task_id=task.id,
        conversation_id=task.conversation_id,
        trigger_message_id=message_id,
        status=ReplyDraftStatus.GENERATED.value,
        source="gate-local-draft-v1",
        draft_text="Привет. Вернусь с ответом чуть позже.",
        prompt_context={"bounded_context": True},
    )

    result = _format_reply_draft_result(draft=draft, task=task)

    assert f"Draft created: {str(draft.id)[:8]}" in result
    assert f"task: {str(task.id)[:8]}" in result
    assert draft.draft_text in result
    assert "Not sent." in result


def test_reply_draft_model_keeps_prompt_context_bounded() -> None:
    message = Message(
        id=uuid.uuid4(),
        bot_id=uuid.uuid4(),
        direction=MessageDirection.INBOUND.value,
        source="telegram",
        event_type="message",
        chat_id="222",
        sender_id="111",
        text="please reply today",
        payload={},
        received_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
        created_at=datetime(2026, 7, 9, 11, 40, tzinfo=UTC),
    )
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        trigger_message_id=message.id,
        status=ReplyTaskStatus.NEEDS_REPLY.value,
        priority=100,
        reason="inbound_human_text",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        reply_task_id=task.id,
        conversation_id=task.conversation_id,
        trigger_message_id=message.id,
        status=ReplyDraftStatus.GENERATED.value,
        source="gate-local-draft-v1",
        draft_text=_build_reply_draft_text(message.text),
        prompt_context={
            "bounded_context": True,
            "source_message": {"id": str(message.id), "text": message.text},
        },
    )

    assert draft.reply_task_id == task.id
    assert draft.status == ReplyDraftStatus.GENERATED.value
    assert draft.source == "gate-local-draft-v1"
    assert draft.prompt_context["bounded_context"] is True
    assert "please reply today" in draft.draft_text
    assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
