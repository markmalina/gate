"""Synthetic reply snapshots, isolation and MCP presentation regression checks."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.api.mcp_routes import _format_reply_context_text
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS, REPLY_DETAILS_SCHEMA
from app.application.mcp_inbox import get_reply_context
from app.application.telegram_replies import extract_scoped_telegram_reply
from app.core.mcp_security import AccessScope
from jsonschema import Draft202012Validator


def payload() -> dict[str, Any]:
    return {"update_id": 100, "business_message": {
        "message_id": 200,
        "chat": {"id": 300},
        "business_connection_id": "synthetic-connection",
        "text": "Please fill the quoted document",
        "reply_to_message": {
            "message_id": 10,
            "chat": {"id": 300},
            "from": {"id": 999, "first_name": "Synthetic", "last_name": "Author"},
            "date": 1_700_000_000,
            "caption": "Original forms",
            "document": {
                "file_name": "../original-forms.docx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "file_size": 1200,
                "file_id": "private-file-token",
                "file_unique_id": "private-unique-token",
            },
            "reply_to_message": {"text": "private-nested-history"},
            "contact": {"vcard": "private-vcard"},
        },
    }}


def extract(raw: dict[str, Any], **overrides: Any) -> dict[str, Any] | None:
    return extract_scoped_telegram_reply(
        raw,
        **({
            "update_id": 100,
            "event_type": "business_message",
            "telegram_message_id": 200,
            "chat_id": "300",
            "business_connection_id": "synthetic-connection",
        } | overrides),
    )


def test_old_document_snapshot_excludes_raw_provider_and_nested_content() -> None:
    result = extract(payload())
    assert result is not None
    assert result["status"] == "snapshot"
    assert result["provider_message_id"] == "10"
    assert result["author"] == "Synthetic Author"
    assert result["caption"] == "Original forms"
    assert result["attachments"][0]["filename"] == "original-forms.docx"
    assert result["attachments"][0]["size_bytes"] == 1200
    assert "private-" not in repr(result)
    assert "file_id" not in repr(result)
    assert "999" not in repr(result)
    Draft202012Validator(REPLY_DETAILS_SCHEMA).validate(result)


def test_partial_quote_keeps_exact_text_and_utf16_position() -> None:
    raw = payload()
    message = raw["business_message"]
    message["reply_to_message"] = {
        "message_id": 10, "chat": {"id": 300}, "text": "😀 Original text\nline two",
    }
    message["quote"] = {"text": "Original text\nline two", "position": 3, "is_manual": True,
                        "entities": [{"type": "text_link", "url": "private-link"}]}
    result = extract(raw)
    assert result is not None
    assert result["text"] == "😀 Original text\nline two"
    assert result["quote"] == {"text": "Original text\nline two", "position": 3, "is_manual": True}
    assert "private-link" not in repr(result)


@pytest.mark.parametrize("overrides", [
    {"update_id": 101}, {"event_type": "message"}, {"telegram_message_id": 201},
    {"chat_id": "301"}, {"business_connection_id": "other-connection"}, {"update_id": True},
])
def test_wrong_parent_identity_returns_no_reply(overrides: dict[str, Any]) -> None:
    assert extract(payload(), **overrides) is None


@pytest.mark.parametrize("change", [
    {"chat": {"id": 301}}, {"chat": {"id": True}}, {"chat": None},
    {"business_connection_id": "other-connection"}, {"message_id": True}, {"message_id": 0},
])
def test_wrong_source_identity_returns_explicit_unavailable(change: dict[str, Any]) -> None:
    raw = payload()
    raw["business_message"]["reply_to_message"].update(change)
    result = extract(raw)
    assert result is not None
    assert result["reason_code"] == "source_identity_mismatch"
    assert result["caption"] is None
    assert result["attachments"] == []
    Draft202012Validator(REPLY_DETAILS_SCHEMA).validate(result)


def test_no_reply_missing_and_external_sources_are_distinguished() -> None:
    raw = payload()
    del raw["business_message"]["reply_to_message"]
    assert extract(raw) is None
    raw["business_message"]["quote"] = {"text": "Quoted fragment", "position": 0}
    missing = extract(raw)
    assert missing is not None and missing["status"] == "unavailable"
    assert missing["quote"]["text"] == "Quoted fragment"
    raw["business_message"]["external_reply"] = {"chat": {"id": 400}, "text": "private-external"}
    external = extract(raw)
    assert external is not None and external["reason_code"] == "external_reply_not_projected"
    assert external["quote"] is None
    assert "private-external" not in repr(external)


def test_no_filename_never_generates_a_name_from_private_provider_id() -> None:
    raw = payload()
    del raw["business_message"]["reply_to_message"]["document"]["file_name"]
    assert extract(raw)["attachments"][0]["filename"] == "telegram-document"


def test_later_exact_source_can_supply_reply_when_pointer_payload_has_none() -> None:
    raw = payload()
    pointer = deepcopy(raw)
    del pointer["business_message"]["reply_to_message"]
    result = extract_scoped_telegram_reply(
        pointer, raw, update_id=100, event_type="business_message", telegram_message_id=200,
        chat_id="300", business_connection_id="synthetic-connection",
    )
    assert result == extract(raw)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def one_or_none(self) -> Any | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return self.rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: [row[0] for row in self.rows])


class FakeSession:
    def __init__(self, batches: list[list[Any]]) -> None:
        self.batches = batches
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.batches.pop(0))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source", ["update", "receipt", "event", "message", "voice", "merged_voice"],
)
async def test_query_schema_and_text_project_reply_outside_history_page(source: str) -> None:
    raw = payload()
    raw["business_message"]["from"] = {"id": 700, "first_name": "Synthetic Sender"}
    has_voice = source in {"voice", "merged_voice"}
    account_id, project_id, task_id, message_id = (UUID(int=i) for i in range(1, 5))
    now = datetime(2026, 10, 8, tzinfo=UTC)
    message = SimpleNamespace(
        id=message_id, bot_id=UUID(int=6), account_id=account_id, project_id=project_id,
        direction="inbound", source="telegram", event_type="business_message", chat_id="300",
        sender_id="700", recipient_id=None, text=raw["business_message"]["text"],
        telegram_update_id=100, telegram_message_id=200,
        business_connection_id="synthetic-connection",
        payload=raw if source in {"message", "merged_voice"} else {},
        created_at=now, received_at=now, sent_at=None,
    )
    conversation = SimpleNamespace(
        id=UUID(int=5), account_id=account_id, project_id=project_id,
        person_id=UUID(int=8) if has_voice else None,
        chat_id="300", business_connection_id="synthetic-connection", title="Synthetic Sender",
    )
    task = SimpleNamespace(id=task_id, status="needs_reply", account_id=account_id,
                           project_id=project_id, person_id=conversation.person_id)
    account = SimpleNamespace(id=account_id, alias="telegram_mark_ru", country_code="RU")
    update = SimpleNamespace(payload=raw) if source == "update" else None
    event = SimpleNamespace(payload=raw) if source == "event" else None
    receipt = SimpleNamespace(payload=raw, update_id=100) if source == "receipt" else None
    session = FakeSession([
        [(task, conversation, message, update, account, "Synthetic Sender")],
        [] if source == "voice" else [(message, update, event, receipt, "Synthetic Sender")],
    ])
    if has_voice:
        archive = SimpleNamespace(
            id=UUID(int=9), telegram_update_id=100, telegram_message_id=200, chat_id="300",
            business_connection_id="synthetic-connection", direction="inbound", received_at=now,
            created_at=now, transcript_text="Synthetic voice transcript",
            transcription_status="transcribed", transcript_language="en", transcribed_at=now,
            transcription_reason_code=None, duration_seconds=5,
        )
        voice_payload = deepcopy(raw)
        if source == "merged_voice":
            del voice_payload["business_message"]["reply_to_message"]
        session.batches.extend([
            [("700",)], [(archive, SimpleNamespace(payload=voice_payload, update_id=100))],
        ])
    context = await get_reply_context(
        session=session, scope=AccessScope(workspace_id="synthetic-workspace",
                                           account_ids=(account_id,), project_ids=(project_id,)),
        task_id=task_id, limit=1,
    )
    assert context is not None
    assert context["count"] == 1
    assert context["messages"][0]["reply_to"] == extract(raw)
    if has_voice:
        assert context["messages"][0]["message_type"] == "voice"
    assert session.batches == []  # No extra history query or provider request.
    Draft202012Validator(MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"]).validate(context)
    rendered = _format_reply_context_text(context)
    assert "reply to message 10 by Synthetic Author" in rendered
    assert "original-forms.docx" in rendered
    assert "Original forms" in rendered
    assert "private-" not in rendered
    history_params = session.statements[1].compile().params
    assert "300" in history_params.values()
    assert account_id in history_params.values()
    assert project_id in history_params.values()
    assert "synthetic-connection" in history_params.values()
