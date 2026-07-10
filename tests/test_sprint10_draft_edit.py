# mypy: ignore-errors
"""Sprint 10 draft edit helper tests."""

import uuid

from app.api import routes as legacy
from app.api.sprint10_routes import (
    _draft_edit_token,
    _extract_edit_draft_ref,
    _render_draft_edit_form,
)
from app.core.config import Settings
from app.models.domain import ReplyDraft, ReplyDraftStatus, ReplyTask, ReplyTaskStatus


def test_edit_draft_ref_parser_accepts_clickable_and_space_forms() -> None:
    assert _extract_edit_draft_ref("/edit_abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/editdraft_abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/edit abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("editdraft abcd1234") == "abcd1234"
    assert _extract_edit_draft_ref("/send_abcd1234") is None


def test_draft_edit_token_is_stable_and_secret_bound() -> None:
    settings = Settings(admin_bearer_token="secret-one")

    token = _draft_edit_token(draft_ref="abcd1234", settings=settings)

    assert token == _draft_edit_token(draft_ref="abcd1234", settings=settings)
    assert token != _draft_edit_token(
        draft_ref="abcd1234", settings=Settings(admin_bearer_token="secret-two")
    )
    assert len(token) == 40


def test_edit_form_contains_existing_draft_text() -> None:
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        trigger_message_id=uuid.uuid4(),
        status=ReplyTaskStatus.NEEDS_REPLY.value,
        priority=100,
        reason="inbound_human_text",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        reply_task_id=task.id,
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        status=ReplyDraftStatus.GENERATED.value,
        source="gate-local-draft-v1",
        draft_text="Привет. Черновик с <html> символами.",
        prompt_context={"bounded_context": True},
    )

    html = _render_draft_edit_form(draft=draft, task=task, token="tok")

    assert "Edit draft" in html
    assert "Save draft" in html
    assert "Привет. Черновик с &lt;html&gt; символами." in html
    assert f"/admin/drafts/{draft.id}/edit?token=tok" in html


def test_draft_creation_output_includes_edit_command_after_sprint10_patch() -> None:
    message_id = uuid.uuid4()
    task = ReplyTask(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        trigger_message_id=message_id,
        status=ReplyTaskStatus.NEEDS_REPLY.value,
        priority=100,
        reason="inbound_human_text",
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

    result = legacy._format_reply_draft_result(draft=draft, task=task)

    assert "Approve send:" in result
    assert f"Edit draft: /edit_{str(draft.id)[:8]}" in result
