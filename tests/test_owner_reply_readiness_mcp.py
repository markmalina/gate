from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.api import mcp_routes
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.application.owner_reply_readiness import (
    OWNER_REPLY_READINESS_TOOL_NAME,
    OwnerReplyReadinessError,
)
from app.application.worker_health import WorkerHealthRegistry
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from jsonschema import Draft202012Validator


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _principal() -> Principal:
    return Principal(
        subject="synthetic-owner",
        scopes=frozenset({"gate:inbox:read"}),
        access_scope=AccessScope(
            workspace_id="mark-personal",
            account_ids=frozenset({UUID(int=101), UUID(int=102)}),
            project_ids=frozenset({UUID(int=103)}),
        ),
        auth_method="oauth2",
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        GATE_MCP_ENABLED=True,
        MCP_AUTH_MODE="oauth",
        MCP_AUTHORIZATION_SERVER="https://auth.gate.example",
        MCP_PUBLIC_RESOURCE_URL="https://gate.example/mcp",
        MCP_OAUTH_JWKS_URL="https://auth.gate.example/.well-known/jwks.json",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"account_alias": "ru", "recipient_chat_id": "1002"},
        {"account_alias": "RU", "recipient_chat_id": 1002},
        {"account_alias": "RU", "recipient_chat_id": "1002", "provider_message_id": None},
        {"account_alias": "RU", "recipient_chat_id": "1002", "provider_message_id": 7},
        {"account_alias": "RU", "recipient_chat_id": "1002", "limit": 10},
        {"account_alias": "RU", "recipient_chat_id": "1002", "confirmed": True},
    ],
)
async def test_invalid_or_broad_inputs_do_not_reach_database_or_resolver(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> None:
    resolver = AsyncMock(side_effect=AssertionError("resolver must not run"))
    monkeypatch.setattr(mcp_routes, "build_owner_reply_readiness", resolver)
    response = await mcp_routes._owner_reply_readiness_tool_result(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=None,
        request=None,
    )
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"] == {"error": "invalid_arguments"}
    Draft202012Validator(MCP_TOOL_OUTPUT_SCHEMAS[OWNER_REPLY_READINESS_TOOL_NAME]).validate(
        response["result"]["structuredContent"]
    )
    resolver.assert_not_awaited()


@pytest.mark.anyio
async def test_exact_metadata_request_uses_existing_registry_and_safe_error_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = _principal()
    session = object()
    registry = WorkerHealthRegistry()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(worker_health_registry=registry))
    )
    resolver = AsyncMock(side_effect=OwnerReplyReadinessError("owner_binding_unavailable"))
    monkeypatch.setattr(mcp_routes, "build_owner_reply_readiness", resolver)
    response = await mcp_routes._owner_reply_readiness_tool_result(
        request_id=2,
        arguments={"account_alias": "RU", "recipient_chat_id": "1002", "provider_message_id": "7"},
        principal=principal,
        session=session,
        request=request,
    )
    resolver.assert_awaited_once_with(
        session=session,
        principal=principal,
        account_alias="RU",
        recipient_chat_id="1002",
        provider_message_id="7",
        worker_registry=registry,
    )
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"] == {"error": "owner_binding_unavailable"}
    Draft202012Validator(MCP_TOOL_OUTPUT_SCHEMAS[OWNER_REPLY_READINESS_TOOL_NAME]).validate(
        response["result"]["structuredContent"]
    )


@pytest.mark.anyio
async def test_readiness_requires_auth_before_resolver_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = AsyncMock(side_effect=AssertionError("unauthenticated resolver call"))
    monkeypatch.setattr(mcp_routes, "build_owner_reply_readiness", resolver)
    response = await mcp_routes.mcp_endpoint(
        payload={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": OWNER_REPLY_READINESS_TOOL_NAME,
                "arguments": {"account_alias": "RU", "recipient_chat_id": "1002"},
            },
        },
        authorization=None,
        settings=_settings(),
        session=None,
        telegram=None,
    )
    assert response["result"]["isError"] is True
    resolver.assert_not_awaited()


@pytest.mark.anyio
async def test_readiness_discovery_is_provider_free_read_only_and_grants_no_new_scope() -> None:
    response = await mcp_routes.mcp_endpoint(
        payload={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        authorization=None,
        settings=_settings(),
        session=None,
        telegram=None,
    )
    tool = next(
        item
        for item in response["result"]["tools"]
        if item["name"] == OWNER_REPLY_READINESS_TOOL_NAME
    )
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tool["securitySchemes"] == [{"type": "oauth2", "scopes": ["gate:inbox:read"]}]
    assert tool["inputSchema"]["additionalProperties"] is False
    assert set(tool["inputSchema"]["properties"]) == {
        "account_alias",
        "recipient_chat_id",
        "provider_message_id",
    }
    Draft202012Validator.check_schema(tool["inputSchema"])
    Draft202012Validator.check_schema(tool["outputSchema"])
