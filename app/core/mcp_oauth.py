"""OAuth access-token validation for the Gate MCP resource server."""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

ASYMMETRIC_JWT_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)


class McpOAuthError(Exception):
    """Safe OAuth error suitable for an MCP WWW-Authenticate challenge."""

    def __init__(self, *, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


async def authenticate_mcp_oauth(
    *, authorization: str | None, settings: Settings, required_scope: str
) -> Principal:
    """Validate a Gate OAuth access token and map its claims to an explicit principal."""

    token = _extract_bearer_token(authorization)
    if token is None:
        raise McpOAuthError(
            error="invalid_token",
            description="A valid Gate access token is required",
        )
    try:
        claims = await asyncio.to_thread(_decode_access_token, token, settings)
        return _principal_from_claims(
            claims=claims,
            settings=settings,
            required_scope=required_scope,
        )
    except McpOAuthError:
        raise
    except (InvalidTokenError, PyJWKClientError, OSError, ValueError) as exc:
        raise McpOAuthError(
            error="invalid_token",
            description="The Gate access token is invalid or expired",
        ) from exc


def _decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    algorithms = _allowed_algorithms(settings.mcp_oauth_allowed_algorithms)
    signing_key = _jwks_client(settings.mcp_oauth_jwks_url).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=algorithms,
        audience=settings.mcp_public_resource_url.rstrip("/"),
        issuer=settings.mcp_authorization_server.rstrip("/"),
        leeway=settings.mcp_oauth_clock_skew_seconds,
        options={
            "require": ["exp", "iat", "iss", "aud", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_nbf": True,
            "verify_iss": True,
            "verify_aud": True,
            "verify_sub": True,
        },
    )
    return dict(claims)


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    if not jwks_url.startswith("https://"):
        raise ValueError("MCP_OAUTH_JWKS_URL must use HTTPS")
    return PyJWKClient(
        jwks_url,
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _allowed_algorithms(value: str) -> list[str]:
    configured = [item.strip() for item in value.split(",") if item.strip()]
    if not configured or any(item not in ASYMMETRIC_JWT_ALGORITHMS for item in configured):
        raise ValueError("MCP OAuth algorithms must be an explicit asymmetric allow-list")
    return configured


def _principal_from_claims(
    *, claims: dict[str, Any], settings: Settings, required_scope: str
) -> Principal:
    scopes = _claim_scopes(claims)
    if required_scope not in scopes:
        raise McpOAuthError(
            error="insufficient_scope",
            description=f"The Gate access token requires scope {required_scope}",
        )

    subject = claims.get("sub")
    workspace_id = claims.get(settings.mcp_oauth_workspace_claim)
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("OAuth subject claim is missing")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ValueError("OAuth workspace grant is missing")

    access_scope = AccessScope(
        workspace_id=workspace_id.strip(),
        account_ids=_uuid_claim(
            claims.get(settings.mcp_oauth_account_ids_claim),
            claim_name=settings.mcp_oauth_account_ids_claim,
        ),
        project_ids=_uuid_claim(
            claims.get(settings.mcp_oauth_project_ids_claim),
            claim_name=settings.mcp_oauth_project_ids_claim,
        ),
    )
    if not access_scope.is_explicit:
        raise ValueError("OAuth access requires explicit workspace, account, and project grants")
    return Principal(
        subject=subject.strip(),
        auth_method="oauth2",
        scopes=scopes,
        access_scope=access_scope,
    )


def _claim_scopes(claims: dict[str, Any]) -> frozenset[str]:
    values: list[str] = []
    for claim_name in ("scope", "scp"):
        claim = claims.get(claim_name)
        if isinstance(claim, str):
            values.extend(item for item in re.split(r"[\s,]+", claim) if item)
        elif isinstance(claim, list) and all(isinstance(item, str) for item in claim):
            values.extend(claim)
    return frozenset(values)


def _uuid_claim(value: Any, *, claim_name: str) -> frozenset[UUID]:
    if isinstance(value, str):
        raw_values = [item for item in re.split(r"[\s,]+", value) if item]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        raw_values = value
    else:
        raise ValueError(f"OAuth {claim_name} claim must contain UUID strings")
    try:
        return frozenset(UUID(item) for item in raw_values)
    except ValueError as exc:
        raise ValueError(f"OAuth {claim_name} claim must contain UUID strings") from exc


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not credential.strip():
        return None
    return credential.strip()
