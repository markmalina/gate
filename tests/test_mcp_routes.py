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
        self.scalar_results = scalar_results or [0, 0, 0, 0, 0, 0, 0, 0]
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


@pytest.fixture
def mcp_context() -> Iterator[FakeMcpSession]:
    session = FakeMcpSession([2, 3, 2, 4, 5, 6, 1, 7])

    async def override_session() -> AsyncIterator[FakeMcpSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    yield session
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def unanswered_context() -> Iterator[FakeMcpSession]:
    now = datetime(2026, 7, 12, 6, 30, tzinfo=UTC)
    task = SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        status="needs_reply",
        priority=10,
        reason="inbound_human_message",
        account_id=UUID("22222222-2222-2222-2222-222222222222"),
        project_id=UUID("33333333-3333-3333-3333-333333333333"),
        created_at=now,
    )
    conversation = SimpleNamespace(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        account_id=UUID("22222222-2222-2222-2222-222222222222"),
        project_id=UUID("33333333-3333-3333-3333-333333333333"),
        chat_id="8648171437",
        business_connection_id="bc-origin",
    )
    message = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
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
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_mcp_initialize_returns_server_info(
    mcp_context: FakeMcpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mcp_context
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/gate_test",
    )
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123456789deadbeef")
    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert payload["result"]["serverInfo"]["name"] == "gate"
    assert payload["result"]["serverInfo"]["version"] == "abc123456789"
    assert payload["result"]["capabilities"] == {"tools": {}}


@pytest.mark.anyio
async def test_mcp_tools_list_exposes_read_only_tools(
    mcp_context: FakeMcpSession,
) -> None:
    del mcp_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
        )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    tool_names = [tool["name"] for tool in tools]
    assert tool_names == ["gate_get_status", "gate_list_unanswered"]
    assert tools[0]["annotations"]["readOnlyHint"] is True
    assert tools[1]["annotations"]["readOnlyHint"] is True
    assert tools[1]["inputSchema"]["properties"]["limit"]["maximum"] == 50


@pytest.mark.anyio
async def test_mcp_status_tool_returns_structured_content(
    mcp_context: FakeMcpSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "fedcba987654321")
    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "tools/call",
                "params": {"name": "gate_get_status", "arguments": {}},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert "Commit: fedcba987654" in result["content"][0]["text"]
    structured = result["structuredContent"]
    assert structured["service"] == "gate"
    assert structured["commit"] == "fedcba987654"
    assert structured["counts"]["accounts"] == 2
    assert structured["counts"]["business_connections"] == 3
    assert structured["counts"]["active_business_connections"] == 2
    assert structured["counts"]["unanswered"] == 1
    assert structured["counts"]["drafts"] == 7
    assert structured["mcp"]["tools"] == ["gate_get_status", "gate_list_unanswered"]
    assert structured["mcp"]["writes_enabled"] is False
    assert structured["mcp"]["send_enabled"] is False
    assert len(mcp_context.statements) == 8


@pytest.mark.anyio
async def test_mcp_list_unanswered_returns_structured_items(
    unanswered_context: FakeMcpSession,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "unanswered-1",
                "method": "tools/call",
                "params": {"name": "gate_list_unanswered", "arguments": {"limit": 5}},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert "Unanswered Gate tasks: 1" in result["content"][0]["text"]
    structured = result["structuredContent"]
    assert structured["count"] == 1
    assert structured["limit"] == 5
    assert structured["next_cursor"] is None
    item = structured["items"][0]
    assert item["task_ref"] == "11111111"
    assert item["status"] == "needs_reply"
    assert item["conversation"]["chat_id"] == "8648171437"
    assert item["message"]["text_preview"] == "Привет, хочу заниматься Око Возрождения"
    assert len(unanswered_context.statements) == 1


@pytest.mark.anyio
async def test_mcp_unknown_tool_returns_jsonrpc_error(
    mcp_context: FakeMcpSession,
) -> None:
    del mcp_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "bad-tool",
                "method": "tools/call",
                "params": {"name": "gate_send_draft", "arguments": {}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "bad-tool"
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "Unknown tool: gate_send_draft"
