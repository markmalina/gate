from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from app.core import mcp_oauth
from app.core.config import Settings
from app.core.mcp_oauth import McpOAuthError, authenticate_mcp_oauth
from cryptography.hazmat.primitives.asymmetric import rsa

ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
ISSUER = "https://auth.gate.example/"
AUDIENCE = "https://gate.example/mcp"


class StaticJwksClient:
    def __init__(self, public_key: Any) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self.public_key)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = StaticJwksClient(private_key.public_key())
    monkeypatch.setattr(mcp_oauth, "_jwks_client", lambda _url: client)
    return private_key


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "MCP_AUTH_MODE": "oauth",
        "MCP_AUTHORIZATION_SERVER": ISSUER,
        "MCP_PUBLIC_RESOURCE_URL": AUDIENCE,
        "MCP_OAUTH_JWKS_URL": f"{ISSUER}.well-known/jwks.json",
    }
    values.update(overrides)
    return Settings(**values)


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    values: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
        "scope": "gate:status:read gate:inbox:read",
        "gate_workspace_id": "mark-personal",
        "gate_account_ids": [ACCOUNT_ID],
        "gate_project_ids": [PROJECT_ID],
    }
    values.update(overrides)
    return values


@pytest.mark.anyio
async def test_valid_jwt_maps_verified_claims_to_explicit_principal(signing_key: Any) -> None:
    token = jwt.encode(_claims(), signing_key, algorithm="RS256", headers={"kid": "test"})

    principal = await authenticate_mcp_oauth(
        authorization=f"Bearer {token}",
        settings=_settings(),
        required_scope="gate:inbox:read",
    )

    assert principal.subject == "user-123"
    assert principal.auth_method == "oauth2"
    assert principal.access_scope.workspace_id == "mark-personal"
    assert {str(item) for item in principal.access_scope.account_ids} == {ACCOUNT_ID}
    assert {str(item) for item in principal.access_scope.project_ids} == {PROJECT_ID}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"iss": "https://attacker.example"},
        {"aud": "https://other.example/mcp"},
        {"exp": datetime.now(tz=UTC) - timedelta(minutes=2)},
    ],
)
async def test_invalid_registered_claims_fail_closed(
    signing_key: Any, claim_overrides: dict[str, Any]
) -> None:
    token = jwt.encode(
        _claims(**claim_overrides),
        signing_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )

    with pytest.raises(McpOAuthError) as exc_info:
        await authenticate_mcp_oauth(
            authorization=f"Bearer {token}",
            settings=_settings(),
            required_scope="gate:inbox:read",
        )

    assert exc_info.value.error == "invalid_token"


@pytest.mark.anyio
async def test_missing_tool_scope_returns_insufficient_scope(signing_key: Any) -> None:
    token = jwt.encode(
        _claims(scope="gate:status:read"),
        signing_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )

    with pytest.raises(McpOAuthError) as exc_info:
        await authenticate_mcp_oauth(
            authorization=f"Bearer {token}",
            settings=_settings(),
            required_scope="gate:inbox:read",
        )

    assert exc_info.value.error == "insufficient_scope"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"gate_workspace_id": ""},
        {"gate_account_ids": ["not-a-uuid"]},
        {"gate_project_ids": ["not-a-uuid"]},
    ],
)
async def test_incomplete_access_grants_fail_closed(
    signing_key: Any, claim_overrides: dict[str, Any]
) -> None:
    token = jwt.encode(
        _claims(**claim_overrides),
        signing_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )

    with pytest.raises(McpOAuthError) as exc_info:
        await authenticate_mcp_oauth(
            authorization=f"Bearer {token}",
            settings=_settings(),
            required_scope="gate:inbox:read",
        )

    assert exc_info.value.error == "invalid_token"


@pytest.mark.anyio
async def test_workspace_only_token_defers_grants_to_gate_database(signing_key: Any) -> None:
    claims = _claims()
    claims.pop("gate_account_ids")
    claims.pop("gate_project_ids")
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test"})

    principal = await authenticate_mcp_oauth(
        authorization=f"Bearer {token}",
        settings=_settings(),
        required_scope="gate:inbox:read",
    )

    assert principal.access_scope.workspace_id == "mark-personal"
    assert principal.access_scope.account_ids == frozenset()
    assert principal.access_scope.project_ids == frozenset()


@pytest.mark.anyio
async def test_symmetric_algorithms_are_never_accepted() -> None:
    token = jwt.encode(_claims(), "a" * 64, algorithm="HS256")

    with pytest.raises(McpOAuthError) as exc_info:
        await authenticate_mcp_oauth(
            authorization=f"Bearer {token}",
            settings=_settings(MCP_OAUTH_ALLOWED_ALGORITHMS="HS256"),
            required_scope="gate:inbox:read",
        )

    assert exc_info.value.error == "invalid_token"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "client_claims", [{}, {"azp": True}, {"client_id": " "}, {"azp": "work", "client_id": "other"}]
)
async def test_personal_scope_requires_unambiguous_verified_client(signing_key, client_claims):
    token = jwt.encode(
        _claims(scope="gate:personal:send_text", **client_claims),
        signing_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    with pytest.raises(McpOAuthError) as error:
        await authenticate_mcp_oauth(
            authorization=f"Bearer {token}",
            settings=_settings(),
            required_scope="gate:personal:send_text",
        )
    assert error.value.error == "invalid_token"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "client_claims", [{"azp": "work"}, {"client_id": "work"}, {"azp": "work", "client_id": "work"}]
)
async def test_personal_principal_keeps_signed_issuer_and_client(signing_key, client_claims):
    token = jwt.encode(
        _claims(scope="gate:personal:send_text", **client_claims),
        signing_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    principal = await authenticate_mcp_oauth(
        authorization=f"Bearer {token}",
        settings=_settings(),
        required_scope="gate:personal:send_text",
    )
    assert principal.oauth_issuer == ISSUER and principal.oauth_client_id == "work"
