# ruff: noqa
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator, FormatChecker

from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.api.mcp_routes import _counterparty, _serialize_connection_health
from app.core.config import get_settings
from app.main import app

TOKEN = "mcp-read-token"
ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _assert_valid_output(tool_name: str, payload: dict[str, Any]) -> None:
    schema = MCP_TOOL_OUTPUT_SCHEMAS[tool_name]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


class UnexpectedTelegramClient:
    """Fail if a read-only MCP test reaches the Telegram provider."""

    async def send_message(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test attempted Telegram send")

    async def get_business_connection(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test queried Telegram")


class FakeMcpResult:
    def __init__(self, scalar: int | None = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self) -> int:
        if self._scalar is None:
            raise AssertionError("Expected scalar result")
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any | None:
        if len(self._rows) > 1:
            raise AssertionError("Expected at most one row")
        return self._rows[0] if self._rows else None


class FakeMcpSession:
    def __init__(
        self,
        scalar_results: list[Any] | None = None,
        row_result_batches: list[list[Any]] | None = None,
    ) -> None:
        self.scalar_results = scalar_results or [0] * 11
        self.row_result_batches = row_result_batches or []
        self.statements: list[Any] = []

    async def execute(self, statement: object) -> FakeMcpResult:
        self.statements.append(statement)
        if self.row_result_batches:
            return FakeMcpResult(rows=self.row_result_batches.pop(0))
        return FakeMcpResult(scalar=self.scalar_results.pop(0))

    async def scalar(self, statement: object) -> int:
        self.statements.append(statement)
        return self.scalar_results.pop(0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def mcp_security_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GATE_MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_MODE", "temporary_bearer")
    monkeypatch.setenv("MCP_READ_BEARER_TOKEN", TOKEN)
    monkeypatch.setenv("MCP_READ_WORKSPACE_ID", "mark-personal")
    monkeypatch.setenv("MCP_READ_ACCOUNT_IDS", ACCOUNT_ID)
    monkeypatch.setenv("MCP_READ_PROJECT_IDS", PROJECT_ID)
    get_settings.cache_clear()
    app.dependency_overrides[get_telegram_client] = lambda: UnexpectedTelegramClient()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def mcp_context() -> Iterator[FakeMcpSession]:
    session = FakeMcpSession(
        [
            2,
            3,
            2,
            4,
            5,
            6,
            1,
            2,
            7,
            0,
            0,
            1,
            0,
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            None,
            0,
        ]
    )

    async def override_session() -> AsyncIterator[FakeMcpSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    yield session


@pytest.fixture
def unanswered_context() -> Iterator[FakeMcpSession]:
    now = datetime(2026, 7, 12, 6, 30, tzinfo=UTC)
    task = SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        status="needs_reply",
        priority=10,
        reason="inbound_human_message",
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        created_at=now,
    )
    conversation = SimpleNamespace(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        chat_id="8648171437",
        business_connection_id="bc-origin",
    )
    message = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        direction="inbound",
        source="telegram",
        chat_id="8648171437",
        sender_id="12345",
        recipient_id=None,
        text="Привет, хочу заниматься Око Возрождения",
        created_at=now,
        received_at=now,
        sent_at=None,
    )
    inbound_update = SimpleNamespace(
        payload={
            "business_message": {
                "from": {
                    "id": 12345,
                    "first_name": "Анна",
                    "username": "anna_example",
                },
                "chat": {"id": 8648171437},
            }
        }
    )
    outbound_message = SimpleNamespace(
        id=UUID("66666666-6666-6666-6666-666666666666"),
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        direction="outbound",
        source="telegram",
        chat_id="8648171437",
        sender_id="7819816320",
        recipient_id="8648171437",
        text="Анна, привет!",
        created_at=now,
        received_at=None,
        sent_at=now,
    )
    outbound_update = SimpleNamespace(
        payload={
            "business_message": {
                "from": {"id": 7819816320, "first_name": "Марк"},
                "chat": {
                    "id": 8648171437,
                    "first_name": "Анна",
                    "username": "anna_example",
                },
            }
        }
    )
    account = SimpleNamespace(
        id=UUID(ACCOUNT_ID),
        alias="telegram_mark_ru",
        country_code="RU",
    )
    session = FakeMcpSession(
        row_result_batches=[
            [
                (
                    task,
                    conversation,
                    message,
                    inbound_update,
                    account,
                    1,
                    "Аня — бухгалтер",
                )
            ],
            [(outbound_message, outbound_update, account, "Аня — бухгалтер")],
        ]
    )

    async def override_session() -> AsyncIterator[FakeMcpSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    yield session


@pytest.fixture
def reply_context() -> Iterator[FakeMcpSession]:
    now = datetime(2026, 7, 12, 6, 30, tzinfo=UTC)
    task = SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        status="needs_reply",
    )
    conversation = SimpleNamespace(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        chat_id="8648171437",
        business_connection_id="bc-origin",
    )
    trigger = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        account_id=UUID(ACCOUNT_ID),
        project_id=UUID(PROJECT_ID),
        direction="inbound",
        source="telegram",
        chat_id="8648171437",
        sender_id="12345",
        recipient_id=None,
        text="Доброе, сделаю что-нибудь с этим",
        created_at=now,
        received_at=now,
        sent_at=None,
    )
    inbound_update = SimpleNamespace(
        payload={
            "business_message": {
                "from": {"id": 12345, "first_name": "Анна"},
                "chat": {"id": 8648171437},
            }
        }
    )
    account = SimpleNamespace(
        id=UUID(ACCOUNT_ID),
        alias="telegram_mark_ru",
        country_code="RU",
    )
    outbound = SimpleNamespace(
        id=UUID("66666666-6666-6666-6666-666666666666"),
        direction="outbound",
        source="telegram",
        chat_id="8648171437",
        sender_id="7819816320",
        recipient_id="8648171437",
        text="Привет, Анна",
        created_at=now,
        received_at=None,
        sent_at=now,
    )
    outbound_update = SimpleNamespace(payload={})
    session = FakeMcpSession(
        row_result_batches=[
            [
                (
                    task,
                    conversation,
                    trigger,
                    inbound_update,
                    account,
                    "Аня — бухгалтер",
                )
            ],
            [
                (outbound, outbound_update, "Аня — бухгалтер"),
                (trigger, inbound_update, "Аня — бухгалтер"),
            ],
        ]
    )

    async def override_session() -> AsyncIterator[FakeMcpSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    yield session


def test_counterparty_prefers_gate_identity_snapshot_over_telegram_profile() -> None:
    message = SimpleNamespace(
        direction="outbound",
        sender_id="owner",
        recipient_id="12345",
        chat_id="12345",
        payload={"_gate_counterparty": {"display_name": "Женя — зал"}},
    )
    telegram_update = SimpleNamespace(
        payload={
            "business_message": {
                "chat": {
                    "id": 12345,
                    "first_name": "Evgeniy",
                    "username": "profile_username",
                }
            }
        }
    )

    counterparty = _counterparty(
        message=message,
        telegram_update=telegram_update,
    )

    assert counterparty["label"] == "Женя — зал"
    assert counterparty["display_name"] == "Женя — зал"
    assert counterparty["username"] == "profile_username"


def test_counterparty_prefers_current_import_over_stale_message_snapshot() -> None:
    message = SimpleNamespace(
        direction="outbound",
        sender_id="owner",
        recipient_id="12345",
        chat_id="12345",
        payload={"_gate_counterparty": {"display_name": "Evgeniy Profile"}},
    )
    telegram_update = SimpleNamespace(
        payload={
            "business_message": {
                "chat": {
                    "id": 12345,
                    "first_name": "Evgeniy",
                    "username": "profile_username",
                }
            }
        }
    )

    counterparty = _counterparty(
        message=message,
        telegram_update=telegram_update,
        resolved_contact_name="Женя — зал",
    )

    assert counterparty["label"] == "Женя — зал"
    assert counterparty["display_name"] == "Женя — зал"
    assert counterparty["username"] == "profile_username"


def test_counterparty_prefers_imported_contact_name_over_telegram_profile() -> None:
    message = SimpleNamespace(
        direction="inbound",
        sender_id="12345",
        recipient_id=None,
        chat_id="12345",
        payload={},
    )
    telegram_update = SimpleNamespace(
        payload={
            "business_message": {
                "from": {
                    "id": 12345,
                    "first_name": "Anna Profile",
                    "username": "profile_username",
                }
            }
        }
    )

    counterparty = _counterparty(
        message=message,
        telegram_update=telegram_update,
        resolved_contact_name="Аня — бухгалтер",
    )

    assert counterparty["label"] == "Аня — бухгалтер"
    assert counterparty["display_name"] == "Аня — бухгалтер"
    assert counterparty["username"] == "profile_username"


def test_connection_health_reports_ready_account_without_exposing_connection_id() -> None:
    now = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    account = SimpleNamespace(
        id=UUID(ACCOUNT_ID),
        alias="telegram_mark_ru",
        country_code="RU",
    )
    connection = SimpleNamespace(
        is_enabled=True,
        rights={"can_reply": True},
        last_update_at=now,
        last_error=None,
        business_connection_id="must-not-leak",
    )

    health = _serialize_connection_health(
        account=account,
        connection=connection,
    )

    assert health["state"] == "ready"
    assert health["can_reply"] is True
    assert "business_connection_id" not in health


async def _post(payload: dict[str, Any], headers: dict[str, str] | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/mcp",
            json=payload,
            headers=AUTH_HEADERS if headers is None else headers,
        )


@pytest.mark.anyio
async def test_mcp_is_disabled_by_default(
    mcp_context: FakeMcpSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mcp_context
    monkeypatch.setenv("GATE_MCP_ENABLED", "false")
    get_settings.cache_clear()
    response = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_mcp_rejects_missing_or_wrong_bearer(mcp_context: FakeMcpSession) -> None:
    del mcp_context
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    missing = await _post(payload, headers={})
    wrong = await _post(payload, headers={"Authorization": "Bearer wrong"})
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401


@pytest.mark.anyio
async def test_mcp_rejects_incomplete_access_scope(
    mcp_context: FakeMcpSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mcp_context
    monkeypatch.setenv("MCP_READ_PROJECT_IDS", "")
    get_settings.cache_clear()
    response = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 503


@pytest.mark.anyio
async def test_mcp_initialize_and_tools_list_are_authenticated(
    mcp_context: FakeMcpSession,
) -> None:
    del mcp_context
    initialize = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    tools_response = await _post({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    assert initialize.status_code == 200
    assert initialize.json()["result"]["serverInfo"]["name"] == "gate"
    tools = tools_response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_save_reply_draft",
        "gate_send_approved_reply",
    ]
    for tool in tools:
        assert tool["outputSchema"] == MCP_TOOL_OUTPUT_SCHEMAS[tool["name"]]
        Draft202012Validator.check_schema(tool["outputSchema"])
    for tool_name in (
        "gate_get_reply_context",
        "gate_save_reply_draft",
        "gate_send_approved_reply",
    ):
        _assert_valid_output(tool_name, {"error": "invalid_arguments"})
    assert tools[0]["annotations"]["readOnlyHint"] is True
    assert tools[1]["annotations"]["readOnlyHint"] is True
    assert tools[2]["annotations"]["readOnlyHint"] is True
    assert tools[3]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert tools[4]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }


@pytest.mark.anyio
async def test_mcp_status_is_scoped(mcp_context: FakeMcpSession) -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {"name": "gate_get_status", "arguments": {}},
        }
    )
    result = response.json()["result"]
    structured = result["structuredContent"]
    _assert_valid_output("gate_get_status", structured)
    assert structured["access"]["workspace_id"] == "mark-personal"
    assert structured["counts"]["accounts"] == 2
    assert structured["counts"]["unanswered"] == 1
    assert structured["mcp"]["auth"] == "temporary_bearer_enforced"
    assert structured["counts"]["answered_messages"] == 2
    assert structured["connection_health"] == []
    assert structured["contact_directory"] == []
    assert structured["alpha_canary"]["one_group_discovered"] is True
    assert structured["alpha_canary"]["one_active_exact_revision"] is False
    assert structured["alpha_canary"]["delivery_enabled"] is False
    assert structured["alpha_canary"]["audit"] == {
        "canonical_messages": 1,
        "agent_turns": 0,
        "delivery_grants": 0,
        "send_attempts": 0,
    }
    model_shadow = structured["alpha_canary"]["model_shadow"]
    assert model_shadow["state"] == "configuration_incomplete"
    assert model_shadow["staged_deployments"] == 1
    assert model_shadow["provider_preflight_verified"] is False
    assert model_shadow["provider_preflight_successes"] == 0
    assert model_shadow["provider_preflight_failures"] == 0
    assert model_shadow["telegram_delivery_blocked"] is True
    assert model_shadow["model_delivery_grants"] == 0
    assert model_shadow["stuck_provider_preflights"] == 0
    assert model_shadow["last_provider_error_category"] == "none"
    assert model_shadow["promotion_dry_run"]["execution_mode"] == "dry_run"
    assert model_shadow["promotion_dry_run"]["mutation_allowed"] is False
    assert model_shadow["budget"] == {
        "monthly_microusd": 50_000_000,
        "reserved_microusd": 0,
        "remaining_microusd": 50_000_000,
    }
    assert "Contact directory:" in result["content"][0]["text"]
    assert "accepted_messages: 1" in result["content"][0]["text"]
    assert "Alpha model shadow:" in result["content"][0]["text"]
    assert "budget_remaining: $50.000000" in result["content"][0]["text"]
    assert "promotion_next: provider_preflight" in result["content"][0]["text"]
    assert len(mcp_context.statements) == 30


@pytest.mark.anyio
async def test_mcp_list_unanswered_returns_only_query_scoped_items(
    unanswered_context: FakeMcpSession,
) -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "unanswered-1",
            "method": "tools/call",
            "params": {"name": "gate_list_unanswered", "arguments": {"limit": 5}},
        }
    )
    result = response.json()["result"]
    _assert_valid_output("gate_list_unanswered", result["structuredContent"])
    assert result["structuredContent"]["count"] == 1
    item = result["structuredContent"]["items"][0]
    assert item["account_id"] == ACCOUNT_ID
    assert item["project_id"] == PROJECT_ID
    assert item["account"] == {
        "id": ACCOUNT_ID,
        "alias": "telegram_mark_ru",
        "label": "RU",
    }
    assert item["message"]["direction"] == "inbound"
    assert item["pending_message_count"] == 1
    assert item["message"]["text_preview"] == "Привет, хочу заниматься Око Возрождения"
    assert item["counterparty"] == {
        "platform": "telegram",
        "platform_user_id": "12345",
        "display_name": "Аня — бухгалтер",
        "username": "anna_example",
        "label": "Аня — бухгалтер",
    }
    assert result["structuredContent"]["answered_count"] == 1
    answered = result["structuredContent"]["answered_items"][0]
    assert answered["message"]["direction"] == "outbound"
    assert answered["message"]["text_preview"] == "Анна, привет!"
    assert answered["counterparty"]["label"] == "Аня — бухгалтер"
    assert answered["account"]["label"] == "RU"
    unanswered_statement = str(unanswered_context.statements[0])
    answered_statement = str(unanswered_context.statements[1])
    assert "reply_tasks.account_id IN" in unanswered_statement
    assert "reply_tasks.project_id IN" in unanswered_statement
    assert "row_number()" in unanswered_statement
    assert "conversations.last_outbound_at" in unanswered_statement
    assert "messages.direction" in unanswered_statement
    assert "messages.direction" in answered_statement
    assert "messages.source IN" in answered_statement
    assert "person_identities.account_id = messages.account_id" in unanswered_statement
    assert "person_identities.account_id = messages.account_id" in answered_statement


@pytest.mark.anyio
async def test_mcp_reply_context_shows_account_and_chronological_history(
    reply_context: FakeMcpSession,
) -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "context-1",
            "method": "tools/call",
            "params": {
                "name": "gate_get_reply_context",
                "arguments": {
                    "task_id": "11111111-1111-1111-1111-111111111111",
                    "limit": 10,
                },
            },
        }
    )

    result = response.json()["result"]
    structured = result["structuredContent"]
    _assert_valid_output("gate_get_reply_context", structured)
    assert structured["account"]["label"] == "RU"
    assert structured["counterparty"]["label"] == "Аня — бухгалтер"
    assert {
        item["counterparty"]["label"] for item in structured["messages"]
    } == {"Аня — бухгалтер"}
    assert [item["direction"] for item in structured["messages"]] == [
        "inbound",
        "outbound",
    ]
    assert "[RU]" in result["content"][0]["text"]
    assert len(reply_context.statements) == 2


@pytest.mark.anyio
async def test_mcp_unknown_tool_returns_jsonrpc_error(mcp_context: FakeMcpSession) -> None:
    del mcp_context
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "bad-tool",
            "method": "tools/call",
            "params": {"name": "gate_send_draft", "arguments": {}},
        }
    )
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "Unknown tool: gate_send_draft"


@pytest.mark.anyio
async def test_temporary_read_bearer_cannot_save_drafts(mcp_context: FakeMcpSession) -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "write-blocked",
            "method": "tools/call",
            "params": {
                "name": "gate_save_reply_draft",
                "arguments": {
                    "task_id": "11111111-1111-1111-1111-111111111111",
                    "text": "Привет!",
                },
            },
        }
    )

    assert response.status_code == 403
    assert mcp_context.statements == []


@pytest.mark.anyio
async def test_temporary_read_bearer_cannot_send_messages(
    mcp_context: FakeMcpSession,
) -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "send-blocked",
            "method": "tools/call",
            "params": {
                "name": "gate_send_approved_reply",
                "arguments": {
                    "draft_id": "44444444-4444-4444-4444-444444444444",
                    "confirmed": True,
                    "expected_account": "RU",
                    "expected_recipient_chat_id": "12345",
                },
            },
        }
    )

    assert response.status_code == 403
    assert mcp_context.statements == []
