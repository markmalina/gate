from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.application.mcp_inbox import (
    _decode_reply_context_cursor,
    _encode_reply_context_cursor,
    _serialize_voice_message,
    _voice_counterparty_id,
    _voice_transcription_status,
    get_reply_context,
)
from app.core.mcp_security import AccessScope
from jsonschema import Draft202012Validator, FormatChecker

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")
PERSON_ID = UUID("44444444-4444-4444-4444-444444444444")
NOW = datetime(2026, 8, 4, 5, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def one_or_none(self) -> Any | None:
        if len(self.rows) > 1:
            raise AssertionError("expected at most one row")
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return self.rows

    def scalars(self) -> SimpleNamespace:
        values = [
            row[0] if isinstance(row, tuple) else row
            for row in self.rows
        ]
        return SimpleNamespace(all=lambda: values)


class FakeSession:
    def __init__(self, batches: list[list[Any]]) -> None:
        self.batches = batches
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        if not self.batches:
            raise AssertionError("unexpected query")
        return FakeResult(self.batches.pop(0))


def message(
    *,
    message_id: str,
    direction: str,
    text: str,
    at: datetime,
    telegram_message_id: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(message_id),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        direction=direction,
        source="telegram",
        chat_id="9001",
        sender_id="77" if direction == "inbound" else "owner",
        recipient_id=None if direction == "inbound" else "77",
        text=text,
        telegram_message_id=telegram_message_id,
        created_at=at,
        received_at=at if direction == "inbound" else None,
        sent_at=at if direction == "outbound" else None,
    )


def archive(
    *,
    archive_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    telegram_message_id: int = 20,
    at: datetime = NOW,
    status: str | None = "transcribed",
    text: str | None = "Голосовой текст",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(archive_id),
        direction="inbound",
        telegram_message_id=telegram_message_id,
        chat_id="9001",
        business_connection_id="bc-1",
        duration_seconds=7,
        transcription_status=status,
        transcript_text=text,
        transcript_language="ru",
        transcription_reason_code=None,
        transcribed_at=at if text else None,
        received_at=at,
        created_at=at,
    )


def task_row() -> tuple[Any, ...]:
    trigger = message(
        message_id="55555555-5555-5555-5555-555555555555",
        direction="inbound",
        text="После голосового",
        at=NOW + timedelta(minutes=1),
        telegram_message_id=21,
    )
    return (
        SimpleNamespace(
            id=TASK_ID,
            status="needs_reply",
            account_id=ACCOUNT_ID,
            project_id=PROJECT_ID,
            person_id=PERSON_ID,
        ),
        SimpleNamespace(
            id=UUID("66666666-6666-6666-6666-666666666666"),
            account_id=ACCOUNT_ID,
            project_id=PROJECT_ID,
            person_id=PERSON_ID,
            chat_id="9001",
            business_connection_id="bc-1",
            title="Катя",
        ),
        trigger,
        SimpleNamespace(
            payload={
                "business_message": {
                    "from": {"id": 77, "first_name": "Катя"},
                    "chat": {"id": 9001},
                }
            }
        ),
        SimpleNamespace(
            id=ACCOUNT_ID, alias="telegram_mark_ru", country_code="RU", telegram_user_id=999,
        ),
        "Катя",
    )


@pytest.mark.parametrize("rank", [0, 1])
def test_reply_cursor_round_trip(rank: int) -> None:
    key = (NOW, rank, UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    encoded = _encode_reply_context_cursor(task_id=TASK_ID, key=key)
    assert _decode_reply_context_cursor(encoded, task_id=TASK_ID) == key
    assert "Голосовой" not in encoded


@pytest.mark.parametrize(
    "cursor",
    ["", "not-base64!", "e30", "a" * 513],
)
def test_reply_cursor_rejects_malformed_values(cursor: str) -> None:
    with pytest.raises(ValueError, match="invalid reply context cursor"):
        _decode_reply_context_cursor(cursor, task_id=TASK_ID)


def test_reply_cursor_is_bound_to_task() -> None:
    cursor = _encode_reply_context_cursor(
        task_id=TASK_ID,
        key=(NOW, 1, UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
    )
    with pytest.raises(ValueError):
        _decode_reply_context_cursor(
            cursor,
            task_id=UUID("99999999-9999-9999-9999-999999999999"),
        )


@pytest.mark.parametrize(
    ("payload", "direction", "expected"),
    [
        ({"business_message": {"from": {"id": 77}}}, "inbound", "77"),
        ({"edited_business_message": {"from": {"id": 78}}}, "inbound", "78"),
        ({"message": {"chat": {"id": 79}}}, "outbound", "79"),
        ({"edited_message": {"chat": {"id": 80}}}, "outbound", "80"),
        ({}, "inbound", None),
        ({"business_message": {"from": {}}}, "inbound", None),
    ],
)
def test_voice_counterparty_identity_is_directional(
    payload: dict[str, Any],
    direction: str,
    expected: str | None,
) -> None:
    assert _voice_counterparty_id(payload, direction=direction) == expected


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (None, None, "pending"),
        ("processing", None, "processing"),
        ("transcribing", None, "processing"),
        ("failed", None, "failed"),
        ("rejected", None, "failed"),
        ("blocked", None, "failed"),
        ("unknown", None, "unknown"),
        ("failed", "Сохранённый результат", "transcribed"),
    ],
)
def test_voice_status_is_normalized_without_invented_text(
    status: str | None,
    text: str | None,
    expected: str,
) -> None:
    assert _voice_transcription_status(
        archive(status=status, text=text)
    ) == expected


def test_voice_serialization_exposes_only_safe_archive_metadata() -> None:
    item = _serialize_voice_message(
        archive=archive(),
        task_id=TASK_ID,
        counterparty_payload={
            "platform": "telegram",
            "platform_user_id": "77",
            "display_name": "Катя",
            "username": None,
            "label": "Катя",
        },
    )
    assert item["message_type"] == "voice"
    assert item["text"] == "Голосовой текст"
    assert item["voice"]["transcription_status"] == "transcribed"
    rendered = repr(item)
    for forbidden in ("file_id", "sha256", "storage", "billing", "provider_operation"):
        assert forbidden not in rendered


@pytest.mark.anyio
async def test_uuid_reply_context_merges_text_and_verified_voice_chronologically() -> None:
    before = message(
        message_id="77777777-7777-7777-7777-777777777777",
        direction="outbound",
        text="До голосового",
        at=NOW - timedelta(minutes=1),
        telegram_message_id=19,
    )
    after = task_row()[2]
    voice = archive()
    receipt = SimpleNamespace(
        payload={
            "business_message": {
                "from": {"id": 77, "first_name": "Катя"},
                "chat": {"id": 9001},
            }
        }
    )
    session = FakeSession(
        [
            [task_row()],
            [
                (after, SimpleNamespace(payload={}), "Катя"),
                (before, SimpleNamespace(payload={}), "Катя"),
            ],
            [("77",)],
            [(voice, receipt)],
        ]
    )
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
    assert [item["message_type"] for item in context["messages"]] == [
        "text",
        "voice",
        "text",
    ]
    assert context["messages"][1]["text"] == "Голосовой текст"
    assert context["has_more"] is False
    assert context["next_cursor"] is None
    Draft202012Validator(
        MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"],
        format_checker=FormatChecker(),
    ).validate(context)
    voice_statement = str(session.statements[3])
    assert "telegram_voice_archives.account_id" in voice_statement
    assert "telegram_voice_archives.project_id" in voice_statement
    assert "telegram_voice_archives.chat_id" in voice_statement
    assert "telegram_voice_archives.business_connection_id" in voice_statement


@pytest.mark.anyio
async def test_unverified_voice_identity_is_excluded() -> None:
    after = task_row()[2]
    session = FakeSession(
        [
            [task_row()],
            [(after, SimpleNamespace(payload={}), "Катя")],
            [],
        ]
    )
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
    assert [item["message_type"] for item in context["messages"]] == ["text"]
    assert len(session.statements) == 3


@pytest.mark.anyio
async def test_ambiguous_or_missing_person_binding_fails_closed() -> None:
    row = list(task_row())
    row[0] = SimpleNamespace(
        id=TASK_ID,
        status="needs_reply",
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        person_id=UUID("99999999-9999-9999-9999-999999999999"),
    )
    after = row[2]
    session = FakeSession(
        [
            [tuple(row)],
            [(after, SimpleNamespace(payload={}), "Катя")],
        ]
    )
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
    assert [item["message_type"] for item in context["messages"]] == ["text"]
    assert len(session.statements) == 2


@pytest.fixture(autouse=True)
def stub_dialog_snapshot_for_chronology_unit_tests(monkeypatch):
    async def snapshot(*args, **kwargs):
        return "dialog-v1:" + "0" * 64
    async def events(*args, **kwargs):
        return [], False
    monkeypatch.setattr("app.application.mcp_inbox.dialog_snapshot", snapshot)
    monkeypatch.setattr("app.application.mcp_inbox.recent_dialog_events", events)
