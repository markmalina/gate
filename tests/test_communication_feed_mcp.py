# ruff: noqa
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.api.communication_feed_mcp import (
    FEED_REPLY_CONTEXT_OUTPUT_SCHEMA,
    _build_feed_payload,
    _decorate_tools_list,
    _parse_feed_day,
    _telegram_source,
)

ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeSession:
    def __init__(self, batches: list[list[object]]) -> None:
        self._batches = batches
        self.statements: list[object] = []

    async def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self._batches.pop(0))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_parse_feed_day_accepts_feed_and_lenta_aliases() -> None:
    expected = date(2026, 7, 25)
    assert _parse_feed_day("feed:day:2026-07-25") == expected
    assert _parse_feed_day("lenta:day:2026-07-25") == expected


@pytest.mark.parametrize(
    "selector",
    [
        "feed:day:25-07-2026",
        "feed:day:2026-02-30",
        "feed:week:2026-07-25",
        "feed:day:2026-7-25",
    ],
)
def test_parse_feed_day_rejects_noncanonical_values(selector: str) -> None:
    with pytest.raises(ValueError):
        _parse_feed_day(selector)


def test_telegram_source_preserves_ru_by_account_identity() -> None:
    assert (
        _telegram_source(SimpleNamespace(alias="telegram_mark_ru", country_code="RU"))
        == "telegram_ru"
    )
    assert (
        _telegram_source(SimpleNamespace(alias="telegram_mark_by", country_code="BY"))
        == "telegram_by"
    )


def test_tools_list_documents_feed_selector_without_new_tool() -> None:
    source = {
        "result": {
            "tools": [
                {
                    "name": "gate_get_reply_context",
                    "inputSchema": {
                        "properties": {
                            "task_id": {"type": "string", "format": "uuid"},
                            "limit": {"type": "integer", "maximum": 20},
                        }
                    },
                    "outputSchema": {},
                }
            ]
        }
    }
    decorated = _decorate_tools_list(source)
    tool = decorated["result"]["tools"][0]
    assert tool["name"] == "gate_get_reply_context"
    assert "format" not in tool["inputSchema"]["properties"]["task_id"]
    assert tool["inputSchema"]["properties"]["limit"]["maximum"] == 500
    assert tool["outputSchema"] == FEED_REPLY_CONTEXT_OUTPUT_SCHEMA
    assert source["result"]["tools"][0]["outputSchema"] == {}


@pytest.mark.anyio
async def test_feed_orders_sources_and_prefers_voice_over_duplicate_message() -> None:
    account_ru = SimpleNamespace(
        id=ACCOUNT_ID,
        alias="telegram_mark_ru",
        country_code="RU",
    )
    person = SimpleNamespace(display_name="Александр Демидчик")
    standalone = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        direction="outbound",
        business_connection_id="bc-ru",
        chat_id="dialog-a",
        telegram_message_id=10,
        text="Уточнил время встречи",
        received_at=None,
        sent_at=datetime(2026, 7, 25, 5, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 25, 5, 0, tzinfo=UTC),
    )
    duplicate_voice_message = SimpleNamespace(
        id=UUID("66666666-6666-6666-6666-666666666666"),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        direction="inbound",
        business_connection_id="bc-ru",
        chat_id="dialog-a",
        telegram_message_id=11,
        text=None,
        received_at=datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
        sent_at=None,
        created_at=datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
    )
    voice = SimpleNamespace(
        id=UUID("77777777-7777-7777-7777-777777777777"),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        direction="inbound",
        business_connection_id="bc-ru",
        chat_id="dialog-a",
        telegram_message_id=11,
        transcript_text="Предложил согласовать обмен валюты и перевозку вещей",
        transcription_status="transcribed",
        received_at=datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
    )
    call = SimpleNamespace(
        id=UUID("88888888-8888-8888-8888-888888888888"),
        project_id=PROJECT_ID,
        transcript_text="Обсудили план встречи и договорились созвониться позже",
        transcription_status="transcribed",
        received_at=datetime(2026, 7, 25, 7, 0, tzinfo=UTC),
    )
    session = FakeSession(
        [
            [
                (standalone, account_ru, person),
                (duplicate_voice_message, account_ru, person),
            ],
            [(voice, account_ru, duplicate_voice_message, person)],
            [(call,)],
        ]
    )
    principal = SimpleNamespace(
        access_scope=SimpleNamespace(
            workspace_id="mark-personal",
            account_ids=frozenset({ACCOUNT_ID}),
            project_ids=frozenset({PROJECT_ID}),
        )
    )

    payload = await _build_feed_payload(
        session=session,
        principal=principal,
        feed_day=date(2026, 7, 25),
        limit=20,
    )

    assert [item["medium"] for item in payload["items"]] == [
        "text",
        "voice",
        "call",
    ]
    assert payload["items"][0]["actor"] == "Марк"
    assert payload["items"][1]["actor"] == "Александр Демидчик"
    assert payload["items"][1]["source"] == "telegram_ru"
    assert payload["items"][2]["direction"] == "mixed"
    assert payload["count"] == 3
    assert payload["present_source_verbatim_by_default"] is False
    assert len(session.statements) == 3

    Draft202012Validator.check_schema(FEED_REPLY_CONTEXT_OUTPUT_SCHEMA)
    Draft202012Validator(
        FEED_REPLY_CONTEXT_OUTPUT_SCHEMA,
        format_checker=FormatChecker(),
    ).validate(payload)
