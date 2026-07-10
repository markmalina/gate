"""Sprint 9 draft send helper tests."""

import uuid

from app.api import routes as legacy
from app.api.sprint9_routes import _extract_send_draft_ref, _format_sprint9_test_guide
from app.models.domain import ReplyDraft, ReplyDraftStatus, ReplyTask, ReplyTaskStatus
from app.services.telegram import _looks_like_html_message


def test_sprint9_test_guide_uses_copyable_code_block() -> None:
    guide = _format_sprint9_test_guide(
        commit_sha="cfec1b9ccbc2622ecb71694bcd38ef372b8fab58",
        bot_username="gate_m_bot",
    )

    assert "Commit: cfec1b9ccbc" in guide
    assert "Bot: gate_m_bot" in guide
    assert "Task: &lt;pending&gt;" in guide
    assert "<pre>test guided draft</pre>" in guide
    assert "Press /send_&lt;draft&gt;" in guide
    assert _looks_like_html_message(guide) is True


def test_send_draft_ref_parser_accepts_clickable_and_space_forms() -> None:
    assert _extract_send_draft_ref("/send_abcd1234") == "abcd1234"
    assert _extract_send_draft_ref("/senddraft_abcd1234") == "abcd1234"
    assert _extract_send_draft_ref("/send abcd1234") == "abcd1234"
    assert _extract_send_draft_ref("senddraft abcd1234") == "abcd1234"
    assert _extract_send_draft_ref("/draft_abcd1234") is None


def test_draft_creation_output_includes_send_command_after_sprint9_patch() -> None:
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

    assert "Draft created:" in result
    assert "Not sent." in result
    assert f"Approve send: /send_{str(draft.id)[:8]}" in result
