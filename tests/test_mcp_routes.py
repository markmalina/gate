# ruff: noqa
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.core.config import get_settings
from app.main import app

TOKEN = "mcp-read-token"
ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


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


class FakeMcpSession:
    def __init__(
        self,
        scalar_results: list[int] | None = None,
        row_results: list[Any] | None = None,
    ) -> None:
        self.scalar_results = scalar_results or [0] * 8
        self.row_results = row_results or []
        self.statements: list[Any] = []

    async def execute(self, statement: object) -> FakeMcpResult:
        self.statements.append(statement)
        if self.row_results:
            return FakeMcpResult(rows=self.row_results)
        return FakeMcpResult(scalar=self.scalar_results.pop(0))


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
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def mcp_context() -> Iterator[FakeMcpSession]:
    session = FakeMcpSession([2, 3, 2, 4, 5, 6, 1, 7])

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
        chat_id="8648171437",
        sender_id="12345",
        text="Привет, хочу заниматься Око Возрождения",
        created_at=now,
        received_at=now,
    )
    session = FakeMcpSession(row_results=[(task, conversation, message)])

    async def override_session() -> AsyncIterator[FakeMcpSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    yield session


async def _post(payload: dict[str, Any], headers: dict[str, str] | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/mcp", json=payload, headers=headers or AUTH_HEADERS)


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
        "gate_save_reply_draft",
    ]
    assert tools[0]["annotations"]["readOnlyHint"] is True
    assert tools[1]["annotations"]["readOnlyHint"] is True
    assert tools[2]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
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
    assert structured["access"]["workspace_id"] == "mark-personal"
    assert structured["counts"]["accounts"] == 2
    assert structured["counts"]["unanswered"] == 1
    assert structured["mcp"]["auth"] == "temporary_bearer_enforced"
    assert len(mcp_context.statements) == 8


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
    assert result["structuredContent"]["count"] == 1
    item = result["structuredContent"]["items"][0]
    assert item["account_id"] == ACCOUNT_ID
    assert item["project_id"] == PROJECT_ID
    assert item["message"]["text_preview"] == "Привет, хочу заниматься Око Возрождения"
    statement = str(unanswered_context.statements[0])
    assert "reply_tasks.account_id IN" in statement
    assert "reply_tasks.project_id IN" in statement


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
