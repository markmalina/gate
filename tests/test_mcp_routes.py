# ruff: noqa
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


@pytest.mark.anyio("asyncio")
async def test_mcp_initialize_returns_server_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/gate_test",
    )
    monkeypatch.setenv("DEPLOYMENT_SHA", "abc123456789deadbeef")
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


@pytest.mark.anyio("asyncio")
async def test_mcp_tools_list_exposes_read_only_status_tool() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
        )

    assert response.status_code == 200
    tool = response.json()["result"]["tools"][0]
    assert tool["name"] == "gate_get_status"
    assert tool["inputSchema"]["type"] == "object"
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False
    assert tool["annotations"]["openWorldHint"] is False


@pytest.mark.anyio("asyncio")
async def test_mcp_status_tool_returns_structured_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOYMENT_SHA", "fedcba987654321")
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
    assert structured["mcp"]["tools"] == ["gate_get_status"]
    assert structured["mcp"]["writes_enabled"] is False
    assert structured["mcp"]["send_enabled"] is False


@pytest.mark.anyio("asyncio")
async def test_mcp_unknown_tool_returns_jsonrpc_error() -> None:
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
