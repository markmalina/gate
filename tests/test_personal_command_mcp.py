"""Signed OAuth ASGI checks for the conditional personal command catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from app.api import mcp_metadata_routes, mcp_routes
from app.api.deps import get_db_session
from app.api.personal_command_mcp import PERSONAL_COMMAND_SCOPES, personal_command_tools
from app.api.routes import get_telegram_client
from app.core import mcp_oauth
from app.core.config import Settings, get_settings
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import TELEGRAM_MARK_BY_ACCOUNT_ID, TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    personal_client_namespace,
)
from app.personal_telegram.command_service import PersonalCommandService
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    PersonalRuntimeBase,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.grant_registry import SqlGrantRegistry
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ISSUER = "https://auth.gate.example/"
RESOURCE = "https://gate.example/mcp"
OPERATION_REF = "operation_" + "a" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ServiceDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failure: Exception | None = None

    async def readiness(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("readiness", kwargs))
        if self.failure is not None:
            raise self.failure
        return {
            "ready": False,
            "runtime_connected": False,
            "blockers": ["runtime_dispatch_not_bound"],
        }

    async def prepare(self, **kwargs: Any) -> dict[str, Any]:
        return await self._operation("prepare", kwargs)

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return await self._operation("execute", kwargs)

    async def status(self, **kwargs: Any) -> dict[str, Any]:
        return await self._operation("status", kwargs)

    async def result(self, **kwargs: Any) -> dict[str, Any]:
        return await self._operation("result", kwargs)

    async def cancel(self, **kwargs: Any) -> dict[str, Any]:
        return await self._operation("cancel", kwargs)

    async def _operation(self, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, kwargs))
        if self.failure is not None:
            raise self.failure
        return {
            "operation_ref": OPERATION_REF,
            "state": "queued",
            "receipt_ref": None,
            "error_code": None,
            "replay": False,
            "sent": False,
            "runtime_connected": False,
        }


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any, ServiceDouble, Settings]:
    settings = Settings(
        _env_file=None,
        GATE_MCP_ENABLED=True,
        GATE_PERSONAL_COMMAND_TOOLS_ENABLED=True,
        MCP_AUTH_MODE="oauth",
        MCP_AUTHORIZATION_SERVER=ISSUER,
        MCP_PUBLIC_RESOURCE_URL=RESOURCE,
        MCP_OAUTH_JWKS_URL=ISSUER + "jwks.json",
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
    )
    monkeypatch.setattr(mcp_oauth, "_jwks_client", lambda _url: jwks)

    async def forbidden_workspace_fallback(**_kwargs: Any) -> Any:
        raise AssertionError("Personal tools must never resolve broad Business grants")

    monkeypatch.setattr(mcp_routes, "_resolve_workspace_principal", forbidden_workspace_fallback)
    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.include_router(mcp_metadata_routes.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_telegram_client] = lambda: None
    service = ServiceDouble()
    app.state.personal_command_service_factory = lambda _principal: service
    return app, key, service, settings


def _token(key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "mark",
        "azp": "work-client",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": " ".join(dict.fromkeys(PERSONAL_COMMAND_SCOPES.values())),
        "gate_workspace_id": "mark-personal",
        "gate_account_ids": [str(TELEGRAM_MARK_RU_ACCOUNT_ID)],
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


async def _call(app: FastAPI, token: str, action: str, arguments: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
    ) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer " + token},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "gate_personal_command_" + action, "arguments": arguments},
            },
        )
        assert response.status_code == 200
        result: dict[str, Any] = response.json()["result"]
        return result


@pytest.mark.anyio
async def test_default_off_and_conditional_catalog(harness: Any) -> None:
    app, _key, service, settings = harness
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
    ) as client:
        settings.gate_personal_command_tools_enabled = False
        response = await client.post("/mcp", json={"id": 1, "method": "tools/list"})
        assert len(response.json()["result"]["tools"]) == 19
        metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert metadata["scopes_supported"] == mcp_metadata_routes.MCP_RESOURCE_SCOPES
        settings.gate_personal_command_tools_enabled = True
        response = await client.post("/mcp", json={"id": 1, "method": "tools/list"})
        tools = response.json()["result"]["tools"]
        assert len(tools) == 25
        assert [tool["name"] for tool in tools[-6:]] == list(PERSONAL_COMMAND_SCOPES)
        for tool in tools[-6:]:
            Draft202012Validator.check_schema(tool["inputSchema"])
            Draft202012Validator.check_schema(tool["outputSchema"])
            assert tool["inputSchema"]["additionalProperties"] is False
        metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert set(PERSONAL_COMMAND_SCOPES.values()) <= set(metadata["scopes_supported"])
        assert metadata["mcp"]["personal_commands"]["runtime_connected"] is False
    assert not service.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action,arguments",
    [
        ("readiness", {}),
        (
            "prepare",
            {
                "grant_ref": "grant_" + "a" * 32,
                "peer_ref": "p" * 100,
                "request_key": "retry:one",
                "text": "synthetic text",
            },
        ),
        ("execute", {"operation_ref": OPERATION_REF}),
        ("status", {"operation_ref": OPERATION_REF}),
        ("result", {"operation_ref": OPERATION_REF}),
        ("cancel", {"operation_ref": OPERATION_REF}),
    ],
)
async def test_signed_oauth_reaches_exact_service_contract(
    harness: Any, action: str, arguments: dict[str, Any]
) -> None:
    app, key, service, settings = harness
    result = await _call(app, _token(key), action, arguments)
    assert result["isError"] is False
    descriptor = next(
        tool
        for tool in personal_command_tools(settings)
        if tool["name"] == "gate_personal_command_" + action
    )
    Draft202012Validator(descriptor["outputSchema"]).validate(result["structuredContent"])
    assert service.calls[-1][0] == action
    assert service.calls[-1][1]["principal"].oauth_client_id == "work-client"
    if action != "readiness":
        assert result["structuredContent"]["sent"] is False
    assert result["structuredContent"]["runtime_connected"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example/"},
        {"aud": "https://other.example/mcp"},
        {"azp": None},
        {"azp": "work-client", "client_id": "attacker-client"},
        {"scope": "gate:messages:send gate:status:read"},
    ],
)
async def test_invalid_identity_never_reaches_service(harness: Any, claims: Any) -> None:
    app, key, service, _settings = harness
    result = await _call(app, _token(key, **claims), "execute", {"operation_ref": OPERATION_REF})
    assert result["isError"] is True
    assert result["structuredContent"]["error"] in {"invalid_token", "insufficient_scope"}
    assert "mcp/www_authenticate" in result["_meta"]
    assert not service.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"operation_ref": OPERATION_REF, "client_namespace": "attacker"},
        {"operation_ref": OPERATION_REF, "confirmed": True},
        {"operation_ref": True},
        {"operation_ref": "1234"},
        None,
        [],
    ],
)
async def test_public_caller_cannot_add_authority(harness: Any, arguments: Any) -> None:
    app, key, service, _settings = harness
    result = await _call(app, _token(key), "execute", arguments)
    assert result["structuredContent"]["error"] == "invalid_arguments"
    assert not service.calls


@pytest.mark.anyio
async def test_disabled_unbound_and_redacted_failures(harness: Any) -> None:
    app, key, service, settings = harness
    token = _token(key)
    settings.gate_personal_command_tools_enabled = False
    result = await _call(app, token, "readiness", {})
    assert result["structuredContent"]["error"] == "personal_command_disabled"
    assert not service.calls
    settings.gate_personal_command_tools_enabled = True
    del app.state.personal_command_service_factory
    result = await _call(app, token, "readiness", {})
    assert result["structuredContent"]["error"] == "personal_command_unbound"
    app.state.personal_command_service_factory = lambda _principal: service
    service.failure = RuntimeError("secret provider detail")
    result = await _call(app, token, "readiness", {})
    assert "secret" not in str(result)
    assert result["structuredContent"]["error"] == "personal_command_failed"
    service.failure = CommandRuntimeError("ru_scope_denied")
    result = await _call(app, token, "readiness", {})
    assert result["structuredContent"]["error"] == "ru_scope_denied"


@pytest.mark.anyio
async def test_signed_asgi_sql_replay_revocation_and_ru_isolation(
    harness: Any, tmp_path: Any
) -> None:
    app, key, _double, settings = harness
    now = datetime.now(UTC)
    scope = RuCommandScope("mark-personal", TELEGRAM_MARK_RU_ACCOUNT_ID, uuid4(), "mark")
    principal = Principal(
        "mark",
        "oauth2",
        frozenset(PERSONAL_COMMAND_SCOPES.values()),
        AccessScope(scope.workspace, frozenset({scope.account_id}), frozenset()),
        ISSUER,
        "work-client",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mcp-contract.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add(
                RuntimeBindingRow(
                    id=scope.binding_id,
                    workspace=scope.workspace,
                    account_id=str(scope.account_id),
                    owner_subject=scope.owner_subject,
                    session_generation=1,
                    ciphertext=b"synthetic-not-a-session",
                    revision=0,
                    key_version="synthetic-v1",
                    custody_state="prepared",
                )
            )
        grants = SqlGrantRegistry(
            sessions, scope=scope, policy_key=Fernet.generate_key(), key_version="synthetic-v1"
        )
        delegation = CommandDelegation(
            uuid4(),
            scope,
            personal_client_namespace(principal),
            frozenset({"send_text"}),
            frozenset({"user:42"}),
            now + timedelta(hours=1),
        )
        await grants.install_approved_grant(delegation, approval_ref="synthetic-approval", now=now)
        store = SqlCommandStore(
            sessions,
            scope=scope,
            payload_cipher=CommandPayloadCipher(Fernet.generate_key()),
            grants=grants,
        )
        service = PersonalCommandService(
            store, grants, peer_reference_key=Fernet.generate_key(), clock=lambda: now
        )
        peer_ref = await service.issue_peer_reference(
            principal=principal, delegation=delegation, peer=PeerIdentity("user", 42), now=now
        )
        app.state.personal_command_service_factory = lambda _principal: service
        args = {
            "grant_ref": "grant_" + delegation.delegation_id.hex,
            "peer_ref": peer_ref,
            "request_key": "host-retry-synthetic",
            "text": "synthetic, never dispatched",
        }
        token = _token(key)
        first = (await _call(app, token, "prepare", args))["structuredContent"]
        retry = (await _call(app, token, "prepare", args))["structuredContent"]
        assert first["operation_ref"] == retry["operation_ref"] and retry["replay"] is True
        conflict = await _call(app, token, "prepare", {**args, "text": "changed intent"})
        assert conflict["isError"] is True
        operation_args = {"operation_ref": first["operation_ref"]}
        queued = await _call(app, token, "execute", operation_args)
        assert queued["structuredContent"]["state"] == "queued"
        assert queued["structuredContent"]["sent"] is False
        status = await _call(app, token, "status", operation_args)
        descriptor = next(
            tool
            for tool in personal_command_tools(settings)
            if tool["name"] == "gate_personal_command_status"
        )
        Draft202012Validator(descriptor["outputSchema"]).validate(status["structuredContent"])
        for claims in (
            {"azp": "foreign-client"},
            {"gate_account_ids": []},
            {"gate_account_ids": [str(TELEGRAM_MARK_BY_ACCOUNT_ID)]},
        ):
            denial = await _call(app, _token(key, **claims), "status", operation_args)
            assert denial["isError"] is True
        cancelled = await _call(app, token, "cancel", operation_args)
        assert cancelled["structuredContent"]["state"] == "cancelled"
        second = (await _call(app, token, "prepare", {**args, "request_key": "second-intent"}))[
            "structuredContent"
        ]
        await grants.revoke(
            principal=principal,
            delegation_id=delegation.delegation_id,
            expected_revision=0,
            now=now,
        )
        assert (await _call(app, token, "result", operation_args))["isError"] is False
        revoked_execute = await _call(
            app, token, "execute", {"operation_ref": second["operation_ref"]}
        )
        assert revoked_execute["isError"] is True
    finally:
        await engine.dispose()
