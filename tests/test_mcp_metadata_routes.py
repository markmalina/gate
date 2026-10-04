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


ACTIVE_MCP_SCOPES = [
    "gate:status:read",
    "gate:inbox:read",
    "gate:drafts:write",
    "gate:messages:send",
    "gate:agents:manage",
]


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
    assert payload["resource"] == "https://gate.example/mcp"
    assert payload["authorization_servers"] == []
    assert payload["scopes_supported"] == ACTIVE_MCP_SCOPES
    assert payload["bearer_methods_supported"] == ["header"]
    assert payload["mcp"]["endpoint"] == "/mcp"
    assert payload["mcp"]["auth_enforced"] is False
    assert payload["mcp"]["write_tools_enabled"] is True
    assert payload["mcp"]["write_tools"] == [
        "gate_save_reply_draft",
        "gate_prepare_reply_document",
        "gate_alpha_control_installation",
        "gate_alpha_run_model_preflight",
        "gate_alpha_get_test_group_presence",
        "gate_beta_get_provider_probe",
    ]
    assert payload["mcp"]["send_tools_enabled"] is True
    assert payload["mcp"]["send_tools"] == [
        "gate_send_approved_reply",
        "gate_send_approved_document",
        "gate_queue_approved_reply_sequence",
        "gate_alpha_send_review",
    ]
    assert payload["mcp"]["send_requires_explicit_confirmation"] is True


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
    assert payload["authorization_servers"] == []
    assert payload["scopes_supported"] == ACTIVE_MCP_SCOPES
    assert payload["mcp"]["read_only_tools"] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_get_owner_reply_readiness",
        "gate_alpha_list_installations",
        "gate_alpha_get_webhook_status",
        "gate_alpha_list_shadow_reviews",
        "gate_alpha_verify_review_replay",
        "gate_beta_export_space_checkpoint",
    ]
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
    assert payload["auth"]["type"] == "temporary_bearer_or_oauth2.1"
    assert payload["auth"]["protected_resource_metadata"] == (
        "https://gate.example/.well-known/oauth-protected-resource/mcp"
    )
    assert payload["auth"]["scopes_supported"] == ACTIVE_MCP_SCOPES
    assert payload["auth"]["current_read_only_scopes"] == [
        "gate:status:read",
        "gate:inbox:read",
    ]
    assert payload["tools"]["read_only"] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_get_owner_reply_readiness",
        "gate_alpha_list_installations",
        "gate_alpha_get_webhook_status",
        "gate_alpha_list_shadow_reviews",
        "gate_alpha_verify_review_replay",
        "gate_beta_export_space_checkpoint",
    ]
    assert payload["tools"]["write"] == [
        "gate_save_reply_draft",
        "gate_prepare_reply_document",
        "gate_alpha_control_installation",
        "gate_alpha_run_model_preflight",
        "gate_alpha_get_test_group_presence",
        "gate_beta_get_provider_probe",
    ]
    assert payload["tools"]["send"] == [
        "gate_send_approved_reply",
        "gate_send_approved_document",
        "gate_queue_approved_reply_sequence",
        "gate_alpha_send_review",
    ]
    assert payload["tools"]["send_requires_explicit_confirmation"] is True


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


@pytest.mark.anyio
async def test_metadata_uses_configured_oauth_issuer_and_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_MODE", "oauth")
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "https://auth.gate.example/")
    monkeypatch.setenv("MCP_PUBLIC_RESOURCE_URL", "https://gate.example/mcp")
    monkeypatch.setenv(
        "MCP_OAUTH_JWKS_URL", "https://auth.gate.example/.well-known/jwks.json"
    )
    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://internal.example") as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

    payload = response.json()
    assert payload["resource"] == "https://gate.example/mcp"
    assert payload["authorization_servers"] == ["https://auth.gate.example/"]
    assert payload["mcp"]["auth_mode"] == "oauth"
    assert payload["mcp"]["auth_status"] == "oauth_token_validation_configured"
