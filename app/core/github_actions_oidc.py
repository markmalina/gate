"""Strict GitHub Actions OIDC authentication for Beta checkpoint jobs."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app.core.config import Settings

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = (
    "https://token.actions.githubusercontent.com/.well-known/jwks"
)
_ALLOWED_EVENTS = frozenset({"schedule", "workflow_dispatch"})
_CURRENT_REPOSITORY = "AlphaCenter/beta"
_LEGACY_REPOSITORY = "markmalina/beta"
_CURRENT_REPOSITORY_OWNER_ID = "310893851"
_LEGACY_REPOSITORY_OWNER_ID = "23397294"
_CURRENT_WORKFLOW_REF = (
    "AlphaCenter/beta/.github/workflows/checkpoint-context.yml@refs/heads/main"
)
_LEGACY_WORKFLOW_REF = (
    "markmalina/beta/.github/workflows/checkpoint-context.yml@refs/heads/main"
)


class GitHubOidcError(RuntimeError):
    """Fail-closed OIDC error with a non-sensitive public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def authenticate_beta_checkpoint_oidc(
    *,
    authorization: str | None,
    settings: Settings,
) -> dict[str, Any]:
    """Authenticate one exact private Beta main-branch workflow."""

    if not settings.gate_beta_checkpoint_oidc_enabled:
        raise GitHubOidcError("beta_checkpoint_oidc_disabled")
    token = _bearer_token(authorization)
    if token is None:
        raise GitHubOidcError("beta_checkpoint_oidc_token_required")
    try:
        claims = await asyncio.to_thread(_decode, token, settings)
    except (InvalidTokenError, PyJWKClientError, OSError, ValueError) as exc:
        raise GitHubOidcError("beta_checkpoint_oidc_invalid") from exc
    _authorize_claims(claims, settings)
    return claims


def _decode(token: str, settings: Settings) -> dict[str, Any]:
    key = _jwks_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=settings.gate_beta_checkpoint_oidc_audience,
        issuer=GITHUB_OIDC_ISSUER,
        leeway=30,
        options={
            "require": [
                "exp",
                "iat",
                "nbf",
                "iss",
                "aud",
                "sub",
                "jti",
                "repository",
                "repository_id",
                "repository_owner_id",
                "repository_visibility",
                "ref",
                "workflow_ref",
                "event_name",
            ]
        },
    )
    return dict(claims)


def _authorize_claims(claims: dict[str, Any], settings: Settings) -> None:
    repository = _canonical_transfer_value(
        settings.gate_beta_checkpoint_oidc_repository,
        legacy=_LEGACY_REPOSITORY,
        current=_CURRENT_REPOSITORY,
    )
    repository_owner_id = _canonical_transfer_value(
        settings.gate_beta_checkpoint_oidc_repository_owner_id,
        legacy=_LEGACY_REPOSITORY_OWNER_ID,
        current=_CURRENT_REPOSITORY_OWNER_ID,
    )
    workflow_ref = _canonical_transfer_value(
        settings.gate_beta_checkpoint_oidc_workflow_ref,
        legacy=_LEGACY_WORKFLOW_REF,
        current=_CURRENT_WORKFLOW_REF,
    )
    repository_owner, separator, repository_name = repository.partition("/")
    if (
        separator != "/"
        or not repository_owner
        or not repository_name
        or "/" in repository_name
    ):
        raise GitHubOidcError("beta_checkpoint_oidc_configuration_invalid")
    expected_subjects = {
        (
            f"repo:{repository}:"
            f"ref:{settings.gate_beta_checkpoint_oidc_ref}"
        ),
        (
            f"repo:{repository_owner}@"
            f"{repository_owner_id}"
            f"/{repository_name}@{settings.gate_beta_checkpoint_oidc_repository_id}"
            f":ref:{settings.gate_beta_checkpoint_oidc_ref}"
        ),
    }
    expected = {
        "repository": repository,
        "repository_id": settings.gate_beta_checkpoint_oidc_repository_id,
        "repository_owner_id": (
            repository_owner_id
        ),
        "repository_visibility": "private",
        "ref": settings.gate_beta_checkpoint_oidc_ref,
        "workflow_ref": workflow_ref,
    }
    if (
        claims.get("sub") not in expected_subjects
        or any(claims.get(name) != value for name, value in expected.items())
    ):
        raise GitHubOidcError("beta_checkpoint_oidc_claim_mismatch")
    if claims.get("event_name") not in _ALLOWED_EVENTS:
        raise GitHubOidcError("beta_checkpoint_oidc_event_not_allowed")


def _canonical_transfer_value(value: str, *, legacy: str, current: str) -> str:
    """Migrate one exact pre-transfer identity value without accepting aliases."""

    return current if value == legacy else value


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    return PyJWKClient(
        GITHUB_OIDC_JWKS_URL,
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if (
        separator != " "
        or scheme.casefold() != "bearer"
        or not credential.strip()
    ):
        return None
    return credential.strip()
