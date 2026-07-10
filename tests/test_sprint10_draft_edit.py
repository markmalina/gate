# mypy: ignore-errors
"""Sprint 10 draft edit helper tests."""

import uuid
from typing import Any

import pytest

from app.api import routes as legacy
from app.api.sprint10_routes import (
    _draft_edit_token,
    _extract_edit_draft_ref,
    _extract_telegram_message_id,
    _is_draft_open,
    _refresh_saved_draft_card,
    _render_draft_edit_form,
)
from app.core.config import Settings
from app.models.domain import (
    AuditLog,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, value: Any) -> None:
        self.added.append(value)


class FakeTelegramClient:
    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []

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


def _make_task_and_draft(
    *,
    draft_status: str = ReplyDraftStatus.GENERATED.value,
    task_status: str = ReplyTaskStatus.NEEDS_REPLY.value,
) -> tuple[ReplyTask, ReplyDraft]:
    message_id = uuid.uuid4()
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        trigger_message_id=message_id,
        status=task_status,
        priority=100,
        reason="inbound_human_text",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        reply_task_id=task.id,
        conversation_id=task.conversation_id,
        trigger_message_id=message_id,
        status=draft_status,
        source="gate-local-draft-v1",
        draft_text="Привет. Вернусь с ответом чуть позже.",
        prompt_context={"bounded_context": True},
    )
    return task, draft


def test_edit_draft_ref_parser_accepts_clickable_and_space_forms() -> None:
    assert _extract_edit_draft_ref("/edit_abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/editdraft_abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/edit abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("editdraft abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/send_abcd1234") is None


def test_draft_edit_token_is_stable_and_secret_bound() -> None:
    settings = Settings(ADMIN_BEARER_TOKEN="secret-one")

    token = _draft_edit_token(draft_ref="abcd1234", settings=settings)

    assert token == _draft_edit_token(draft_ref="abcd1234", settings=settings)
    assert token != _draft_edit_token(
        draft_ref="abcd1234", settings=Settings(ADMIN_BEARER_TOKEN="secret-two")
    )
    assert len(token) == 40


def test_edit_form_contains_existing_draft_text() -> None:
    task, draft = _make_task_and_draft()
    draft.draft_text = "Привет. Черновик с <html> символами."

    html = _render_draft_edit_form(draft=draft, task=task, token="tok")

    assert "Edit draft" in html
    assert "Save draft" in html
    assert "Привет. Черновик с &lt;html&gt; символами." in html
    assert f"/admin/drafts/{draft.id}/edit?token=tok" in html


def test_draft_creation_output_includes_edit_command_after_sprint10_patch() -> None:
    task, draft = _make_task_and_draft()

    result = legacy._format_reply_draft_result(draft=draft, task=task)

    assert "Approve send:" in result
    assert f"Edit draft: /edit_{str(draft.id)[:8]}" in result


def test_generated_and_edited_drafts_are_open_while_task_needs_reply() -> None:
    task, generated = _make_task_and_draft()
    _task, edited = _make_task_and_draft(draft_status=ReplyDraftStatus.EDITED.value)

    assert _is_draft_open(draft=generated, task=task)
    assert _is_draft_open(draft=edited, task=task)


def test_approved_draft_or_replied_task_is_closed() -> None:
    open_task, approved = _make_task_and_draft(draft_status=ReplyDraftStatus.APPROVED.value)
    replied_task, generated = _make_task_and_draft(task_status=ReplyTaskStatus.REPLIED.value)

    assert not _is_draft_open(draft=approved, task=open_task)
    assert not _is_draft_open(draft=generated, task=replied_task)


def test_extract_telegram_message_id_from_send_and_nested_edit_payloads() -> None:
    assert _extract_telegram_message_id({"result": {"message_id": 42}}) == 42
    assert (
        _extract_telegram_message_id(
            {
                "original_send": {"result": {"message_id": 42}},
                "latest_edit": {"result": {"message_id": 43}},
            }
        )
        == 43
    )
    assert _extract_telegram_message_id({"result": {}}) is None


@pytest.mark.anyio
async def test_refresh_saved_draft_card_edits_original_telegram_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, draft = _make_task_and_draft()
    draft.draft_text = "Новый сохранённый текст."
    message = Message(
        id=uuid.uuid4(),
        bot_id=uuid.uuid4(),
        gate_event_id=uuid.uuid4(),
        direction=MessageDirection.OUTBOUND.value,
        source="gate",
        event_type="telegram_admin_console_reply",
        chat_id="7819816320",
        sender_id="gate_m_bot",
        recipient_id="7819816320",
        text=f"Draft created: {str(draft.id)[:8]}\nold text",
        business_connection_id="business-1",
        payload={"ok": True, "result": {"message_id": 777}},
    )
    session = FakeSession()
    telegram = FakeTelegramClient()

    async def fake_find_latest_draft_card_message(*, session: Any, draft: ReplyDraft) -> Message:
        del session, draft
        return message

    monkeypatch.setattr(
        "app.api.sprint10_routes._find_latest_draft_card_message",
        fake_find_latest_draft_card_message,
    )

    await _refresh_saved_draft_card(
        session=session,
        telegram=telegram,
        draft=draft,
        task=task,
    )

    assert telegram.edits == [
        {
            "chat_id": "7819816320",
            "message_id": 777,
            "text": message.text,
            "business_connection_id": "business-1",
        }
    ]
    assert "Новый сохранённый текст." in message.text
    assert message.telegram_message_id == 777
    assert message.payload["original_send"]["result"]["message_id"] == 777
    assert message.payload["latest_edit"]["result"]["message_id"] == 777
    assert any(
        isinstance(value, AuditLog) and value.action == "telegram_draft_card_refreshed"
        for value in session.added
    )
