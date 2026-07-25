# ruff: noqa
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.application.reply_drafts import SavedReplyDraft
from app.core.config import get_settings
from app.core.mcp_security import AccessScope, Principal
from app.main import app


class UnexpectedTelegramClient:
    """Fail if a read-only MCP test reaches the Telegram provider."""

    async def send_message(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test attempted Telegram send")

    async def get_business_connection(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test queried Telegram")


class NoQuerySession:
    async def execute(self, _statement: object) -> None:
        raise AssertionError("OAuth discovery must not query Gate data")


class CountSession:
    def __init__(self) -> None:
        self.query_count = 0

    async def execute(self, _statement: object) -> Any:
        self.query_count += 1

        class Result:
            def scalar_one(self) -> int:
                return 0

            def all(self) -> list[object]:
                return []

        return Result()

    async def scalar(self, _statement: object) -> int:
        self.query_count += 1
        return 0


def _voice_status_payload() -> dict[str, Any]:
    return {
        "state": "disabled",
        "enabled": False,
        "api_key_configured": False,
        "model": "gpt-4o-mini-transcribe",
        "entitlement_active": True,
        "period_key": "2026-07",
        "hard_limit_microusd": 1_000_000,
        "settled_microusd": 0,
        "reserved_microusd": 0,
        "remaining_microusd": 1_000_000,
        "settled_count": 0,
        "open_count": 0,
        "unknown_count": 0,
        "reconciliation_required": False,
        "received_count": 3,
        "processing_count": 0,
        "delivered_count": 1,
        "retry_count": 0,
        "dead_letter_count": 2,
        "last_error_code": "openai_transcription_rejected",
        "telegram_delivery_allowed": False,
    }


class CommitSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


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
    app.dependency_overrides[get_telegram_client] = lambda: UnexpectedTelegramClient()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def _post(payload: dict[str, Any], authorization: str | None = None):
    headers = {"Authorization": authorization} if authorization else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        return await client.post("/mcp", json=payload, headers=headers)


async def _get(path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_oauth_metadata_advertises_only_active_scopes_and_tools() -> None:
    response = await _get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scopes_supported"] == [
        "gate:status:read",
        "gate:inbox:read",
        "gate:drafts:write",
        "gate:messages:send",
        "gate:agents:manage",
    ]
    assert payload["mcp"]["read_only_tools"] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_alpha_list_installations",
        "gate_alpha_get_webhook_status",
        "gate_alpha_list_shadow_reviews",
        "gate_alpha_verify_review_replay",
    ]
    assert payload["mcp"]["send_tools"] == [
        "gate_send_approved_reply",
        "gate_queue_approved_reply_sequence",
        "gate_alpha_send_review",
    ]
    assert payload["mcp"]["send_requires_explicit_confirmation"] is True


@pytest.mark.anyio
async def test_chatgpt_can_initialize_and_scan_tools_before_oauth() -> None:
    initialize = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    tools_response = await _post(
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"}
    )

    assert initialize.status_code == 200
    tools = tools_response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_save_reply_draft",
        "gate_send_approved_reply",
        "gate_queue_approved_reply_sequence",
        "gate_alpha_list_installations",
        "gate_alpha_get_webhook_status",
        "gate_alpha_control_installation",
        "gate_alpha_run_model_preflight",
        "gate_alpha_list_shadow_reviews",
        "gate_alpha_get_test_group_presence",
        "gate_alpha_send_review",
        "gate_alpha_verify_review_replay",
    ]
    assert tools[0]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[0]["_meta"]["securitySchemes"] == tools[0]["securitySchemes"]
    assert tools[1]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:inbox:read"]}
    ]
    assert tools[2]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:inbox:read"]}
    ]
    assert tools[3]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:drafts:write"]}
    ]
    assert tools[4]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:messages:send"]}
    ]
    assert tools[5]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:messages:send"]}
    ]
    assert tools[6]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[7]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[8]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[9]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[10]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[3]["annotations"]["destructiveHint"] is False
    assert "never approves or sends" in tools[3]["description"]
    assert tools[4]["annotations"]["idempotentHint"] is True
    assert tools[4]["annotations"]["openWorldHint"] is True


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
async def test_verified_oauth_principal_can_call_scoped_read_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = CountSession()

    async def override_session() -> AsyncIterator[CountSession]:
        yield session

    async def authenticate(**kwargs: Any) -> Principal:
        assert kwargs["authorization"] == "Bearer signed-token"
        assert kwargs["required_scope"] == "gate:status:read"
        return Principal(
            subject="user-123",
            auth_method="oauth2",
            scopes=frozenset({"gate:status:read"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({UUID("22222222-2222-2222-2222-222222222222")}),
                project_ids=frozenset({UUID("33333333-3333-3333-3333-333333333333")}),
            ),
        )

    async def voice_status(_session: object, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(payload=_voice_status_payload)

    async def parity_status(_session: object, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            payload=lambda: {
                "state": "no_evidence",
                "total_count": 0,
                "matched_count": 0,
                "pending_count": 0,
                "mismatch_count": 0,
                "required_account_count": 0,
                "covered_account_count": 0,
                "mismatch_codes": [],
                "cutover_ready": False,
                "private_content_exposed": False,
            }
        )

    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
    monkeypatch.setattr(
        "app.api.mcp_routes.telegram_voice_operational_status",
        voice_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.telegram_business_text_parity_status",
        parity_status,
    )
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "oauth-call",
            "method": "tools/call",
            "params": {"name": "gate_get_status", "arguments": {}},
        },
        authorization="Bearer signed-token",
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["mcp"]["auth"] == "oauth2_enforced"
    assert session.query_count == 31


@pytest.mark.anyio
async def test_verified_oauth_principal_can_save_draft_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = CommitSession()
    task_id = UUID("11111111-1111-1111-1111-111111111111")
    account_id = UUID("22222222-2222-2222-2222-222222222222")
    project_id = UUID("33333333-3333-3333-3333-333333333333")

    async def override_session() -> AsyncIterator[CommitSession]:
        yield session

    async def authenticate(**kwargs: Any) -> Principal:
        assert kwargs["required_scope"] == "gate:drafts:write"
        return Principal(
            subject="user-123",
            auth_method="oauth2",
            scopes=frozenset({"gate:drafts:write"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({account_id}),
                project_ids=frozenset({project_id}),
            ),
        )

    class Writer:
        def __init__(self, _session: Any) -> None:
            pass

        async def save(self, command: Any) -> SavedReplyDraft:
            return SavedReplyDraft(
                draft_id=UUID("44444444-4444-4444-4444-444444444444"),
                task_id=command.task_id,
                account_id=account_id,
                project_id=project_id,
                account_alias="telegram_mark_ru",
                account_label="RU",
                recipient_chat_id="12345",
                status="generated",
                text=command.text,
            )

    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
    monkeypatch.setattr("app.api.mcp_routes.SqlReplyDraftWriter", Writer)
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "draft-call",
            "method": "tools/call",
            "params": {
                "name": "gate_save_reply_draft",
                "arguments": {"task_id": str(task_id), "text": "  Привет!  "},
            },
        },
        authorization="Bearer signed-token",
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["text"] == "Привет!"
    assert result["structuredContent"]["sent"] is False
    assert result["structuredContent"]["approval_required"] is True
    assert result["structuredContent"]["send_available"] is True
    assert result["structuredContent"]["account"]["label"] == "RU"
    assert result["structuredContent"]["recipient_chat_id"] == "12345"
    assert result["structuredContent"]["confirmation"] == {
        "expected_account": "RU",
        "expected_recipient_chat_id": "12345",
    }
    assert session.commits == 1


@pytest.mark.anyio
async def test_oauth_mode_fails_closed_when_discovery_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "")
    get_settings.cache_clear()
    response = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP OAuth validation is not configured"
