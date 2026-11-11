"""Synthetic contact-card readback without real personal details or provider calls."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.application.mcp_inbox import get_reply_context
from app.application.telegram_contacts import extract_scoped_telegram_contact
from app.core.mcp_security import AccessScope
from jsonschema import Draft202012Validator, FormatChecker

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")
PERSON_ID = UUID("44444444-4444-4444-4444-444444444444")
MESSAGE_ID = UUID("55555555-5555-5555-5555-555555555555")
NOW = datetime(2026, 9, 23, 5, 21, tzinfo=UTC)


def update(
    *,
    event_type: str = "business_message",
    contact: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "update_id": 10,
        event_type: {
            "message_id": 20,
            "chat": {"id": 30, "type": "private"},
            "business_connection_id": "business-ru",
            "contact": contact
            if contact is not None
            else {
                "first_name": "Тест",
                "last_name": "Контакт",
                "phone_number": "+000 123 456",
                "user_id": 123456,
                "vcard": "BEGIN:VCARD\\nNOTE:private synthetic note\\nEND:VCARD",
            },
        },
    }


def extract(payload: dict[str, object], **overrides: Any) -> dict[str, object] | None:
    return extract_scoped_telegram_contact(
        payload,
        update_id=overrides.get("update_id", 10),
        event_type=overrides.get("event_type", "business_message"),
        telegram_message_id=overrides.get("telegram_message_id", 20),
        chat_id=overrides.get("chat_id", "30"),
        business_connection_id=overrides.get("business_connection_id", "business-ru"),
    )


def test_contact_fields_are_bounded_and_vcard_body_is_never_returned() -> None:
    card = extract(update())
    assert card == {
        "first_name": "Тест",
        "last_name": "Контакт",
        "phone_number": "+000 123 456",
        "user_id": "123456",
        "vcard_present": True,
    }
    assert "private synthetic note" not in repr(card)
    assert extract(
        update(contact={"first_name": "Один", "phone_number": "+000"})
    ) == {
        "first_name": "Один",
        "last_name": None,
        "phone_number": "+000",
        "user_id": None,
        "vcard_present": False,
    }


def test_contact_only_matches_exact_message_and_not_generic_attachment() -> None:
    exact = update()
    for mismatch in (
        {"update_id": 11},
        {"event_type": "message"},
        {"telegram_message_id": 21},
        {"chat_id": "31"},
        {"business_connection_id": "business-by"},
    ):
        assert extract(exact, **mismatch) is None
    assert extract({"update_id": 10, "business_message": {
        "message_id": 20,
        "chat": {"id": 30},
        "business_connection_id": "business-ru",
        "document": {"file_id": "synthetic"},
    }}) is None
    assert extract(update(contact={"phone_number": "+000"})) is None
    assert extract(
        update(contact={"first_name": "Тест", "phone_number": "x\nsecret"})
    ) is None


def test_repeated_delivery_projects_one_contact_without_duplicate_data() -> None:
    raw = update()
    card = extract_scoped_telegram_contact(
        raw,
        raw,
        update_id=10,
        event_type="business_message",
        telegram_message_id=20,
        chat_id="30",
        business_connection_id="business-ru",
    )
    assert card == extract(raw)
    assert isinstance(card, dict)
    assert len(card) == 5


def test_outbound_and_ordinary_message_contact_use_same_exact_contract() -> None:
    card = extract(update(event_type="message"), event_type="message")
    assert card is not None
    assert card["first_name"] == "Тест"


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

    async def execute(self, _statement: Any) -> FakeResult:
        if not self.batches:
            raise AssertionError("unexpected query")
        return FakeResult(self.batches.pop(0))


@pytest.mark.anyio
async def test_existing_durable_update_reads_as_scoped_contact() -> None:
    message = SimpleNamespace(
        id=MESSAGE_ID,
        bot_id=UUID("66666666-6666-6666-6666-666666666666"),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        direction="inbound",
        source="telegram",
        event_type="business_message",
        chat_id="30",
        sender_id="77",
        recipient_id=None,
        text=None,
        telegram_update_id=10,
        telegram_message_id=20,
        business_connection_id="business-ru",
        payload={"raw_update_ref": "telegram_updates"},
        created_at=NOW,
        received_at=NOW,
        sent_at=None,
    )
    conversation = SimpleNamespace(
        id=UUID("77777777-7777-7777-7777-777777777777"),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        person_id=PERSON_ID,
        chat_id="30",
        business_connection_id="business-ru",
        title="Отправитель",
    )
    task = SimpleNamespace(
        id=TASK_ID,
        status="needs_reply",
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        person_id=PERSON_ID,
    )
    stored_update = SimpleNamespace(payload=update())
    session = FakeSession([
        [(task, conversation, message, stored_update,
          SimpleNamespace(
            id=ACCOUNT_ID, alias="telegram_mark_ru", country_code="RU", telegram_user_id=999,
        ),
          "Отправитель")],
        [(message, stored_update, None, None, "Отправитель")],
        [],
    ])
    context = await get_reply_context(
        session=session,
        scope=AccessScope(
            workspace_id="mark-personal",
            account_ids=(ACCOUNT_ID,),
            project_ids=(PROJECT_ID,),
        ),
        task_id=TASK_ID,
        limit=20,
    )
    assert context is not None
    item = context["messages"][0]
    assert item["message_type"] == "contact"
    assert item["text"] is None
    assert item["text_preview"] == "Получен контакт"
    assert item["contact"]["phone_number"] == "+000 123 456"
    assert item["contact"]["source_ref"] == f"telegram-message:{MESSAGE_ID}"
    assert item["contact"]["source_message_id"] == 20
    assert item["attachments"] == []
    assert len(context["messages"]) == 1
    assert session.batches == []
    Draft202012Validator(
        MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"],
        format_checker=FormatChecker(),
    ).validate(context)


@pytest.fixture(autouse=True)
def stub_dialog_snapshot_for_chronology_unit_tests(monkeypatch):
    async def snapshot(*args, **kwargs):
        return "dialog-v1:" + "0" * 64
    async def events(*args, **kwargs):
        return [], False, True
    monkeypatch.setattr("app.application.mcp_inbox.dialog_snapshot", snapshot)
    monkeypatch.setattr("app.application.mcp_inbox.recent_dialog_events", events)


@pytest.fixture(autouse=True)
def stub_readset_issue_for_chronology_units(monkeypatch):
    async def issue(*args, **kwargs):
        return None
    monkeypatch.setattr("app.application.mcp_inbox.dialog_read_issue", issue)
