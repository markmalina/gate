# ruff: noqa
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_oauth_protected_resource_metadata() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "gate.example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == "https://gate.example"
    assert payload["authorization_servers"] == [
        "https://gate.example/.well-known/oauth-authorization-server"
    ]
    assert "gate:status:read" in payload["scopes_supported"]
    assert "gate:messages:send" in payload["scopes_supported"]
    assert payload["bearer_methods_supported"] == ["header"]
    assert payload["mcp"]["endpoint"] == "/mcp"
    assert payload["mcp"]["auth_enforced"] is False
    assert payload["mcp"]["write_tools_enabled"] is False
    assert payload["mcp"]["send_tools_enabled"] is False


@pytest.mark.anyio
async def test_oauth_protected_resource_mcp_metadata() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "gate.example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == "https://gate.example/mcp"
    assert payload["authorization_servers"] == [
        "https://gate.example/.well-known/oauth-authorization-server"
    ]
    assert "gate:inbox:read" in payload["scopes_supported"]
    assert payload["mcp"]["read_only_tools"] == ["gate_get_status", "gate_list_unanswered"]
    assert payload["mcp"]["auth_status"] == "disabled_by_default"


@pytest.mark.anyio
async def test_mcp_metadata_debug_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123456789deadbeef")
    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        response = await client.get(
            "/mcp/metadata",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "gate.example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "gate"
    assert payload["resource"] == "https://gate.example/mcp"
    assert payload["commit"] == "abc123456789"
    assert payload["auth"]["enforced"] is False
    assert payload["auth"]["type"] == "temporary_bearer_oauth2.1_planned"
    assert payload["auth"]["protected_resource_metadata"] == (
        "https://gate.example/.well-known/oauth-protected-resource/mcp"
    )
    assert payload["auth"]["current_read_only_scopes"] == [
        "gate:status:read",
        "gate:inbox:read",
    ]
    assert payload["tools"]["read_only"] == ["gate_get_status", "gate_list_unanswered"]
    assert payload["tools"]["write"] == []
    assert payload["tools"]["send"] == []


@pytest.mark.anyio
async def test_mcp_metadata_reports_temporary_bearer_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_MCP_ENABLED", "true")
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        response = await client.get("/mcp/metadata")

    payload = response.json()
    assert payload["auth"]["enabled"] is True
    assert payload["auth"]["enforced"] is True
    assert payload["auth"]["status"] == "temporary_bearer_enforced"
