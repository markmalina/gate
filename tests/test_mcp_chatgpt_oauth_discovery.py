# ruff: noqa
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.core.config import get_settings
from app.main import app


class NoQuerySession:
    async def execute(self, _statement: object) -> None:
        raise AssertionError("OAuth discovery must not query Gate data")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def oauth_discovery_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GATE_MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_MODE", "oauth")
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "https://auth.gate.example")
    monkeypatch.setenv("MCP_PUBLIC_RESOURCE_URL", "https://gate.example/mcp")
    monkeypatch.setenv("MCP_OAUTH_JWKS_URL", "https://auth.gate.example/.well-known/jwks.json")
    get_settings.cache_clear()

    async def override_session() -> AsyncIterator[NoQuerySession]:
        yield NoQuerySession()

    app.dependency_overrides[get_db_session] = override_session
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def _post(payload: dict[str, Any], authorization: str | None = None):
    headers = {"Authorization": authorization} if authorization else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        return await client.post("/mcp", json=payload, headers=headers)


@pytest.mark.anyio
async def test_chatgpt_can_initialize_and_scan_tools_before_oauth() -> None:
    initialize = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    tools_response = await _post(
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"}
    )

    assert initialize.status_code == 200
    tools = tools_response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["gate_get_status", "gate_list_unanswered"]
    assert tools[0]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[0]["_meta"]["securitySchemes"] == tools[0]["securitySchemes"]
    assert tools[1]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:inbox:read"]}
    ]


@pytest.mark.anyio
async def test_tool_call_returns_chatgpt_oauth_challenge_without_data() -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "oauth-required",
            "method": "tools/call",
            "params": {"name": "gate_list_unanswered", "arguments": {}},
        }
    )

    result = response.json()["result"]
    assert result["isError"] is True
    challenge = result["_meta"]["mcp/www_authenticate"][0]
    assert (
        'resource_metadata="https://gate.example/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert 'scope="gate:inbox:read"' in challenge
    assert 'error="invalid_token"' in challenge
    assert "error_description=" in challenge


@pytest.mark.anyio
async def test_oauth_mode_fails_closed_when_discovery_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "")
    get_settings.cache_clear()
    response = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP OAuth validation is not configured"
