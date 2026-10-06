"""Distinct fixed private-Mark authorizer; Beta authorization is unchanged."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app.core.config import Settings
from app.core.github_actions_oidc import (
    GITHUB_OIDC_ISSUER,
    GitHubOidcError,
    _bearer_token,
    _jwks_client,
)

AUDIENCE = "gate-watch-voice-drain-v1"
REPOSITORY = "markmalina/mark"
REPOSITORY_ID = "1336157811"
OWNER_ID = "23397294"
WORKFLOW_PATH = ".github/workflows/gate-watch-voice-drain.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main"


def authorize_claims(claims: dict[str, Any], *, now: float | None = None) -> None:
    expected = {
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "repository_owner_id": OWNER_ID,
        "repository_visibility": "private",
        "ref": "refs/heads/main",
        "workflow_ref": WORKFLOW_REF,
        "run_attempt": "1",
        "runner_environment": "github-hosted",
    }
    subjects = {
        f"repo:{REPOSITORY}:ref:refs/heads/main",
        f"repo:markmalina@{OWNER_ID}/mark@{REPOSITORY_ID}:ref:refs/heads/main",
    }
    current = time.time() if now is None else now
    issued = claims.get("iat")
    if not isinstance(issued, (int, float)) or isinstance(issued, bool):
        raise GitHubOidcError("watch_voice_oidc_claim_mismatch")
    if (
        any(claims.get(key) != value for key, value in expected.items())
        or claims.get("sub") not in subjects
        or claims.get("event_name")
        not in {"schedule", "workflow_dispatch", "push", "issue_comment"}
        or (
            claims.get("event_name") in {"workflow_dispatch", "push", "issue_comment"}
            and claims.get("actor_id") != OWNER_ID
        )
        or not isinstance(claims.get("run_id"), str)
        or re.fullmatch(r"[1-9][0-9]{0,19}", claims["run_id"]) is None
        or not isinstance(claims.get("sha"), str)
        or re.fullmatch(r"[a-f0-9]{40}", claims["sha"]) is None
        or not isinstance(claims.get("jti"), str)
        or not 1 <= len(claims["jti"]) <= 128
        or not -30 <= current - issued <= 300
    ):
        raise GitHubOidcError("watch_voice_oidc_claim_mismatch")


def _decode(token: str) -> dict[str, Any]:
    key = _jwks_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=AUDIENCE,
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
                "run_attempt",
                "run_id",
                "sha",
                "runner_environment",
            ]
        },
    )
    return dict(claims)


async def authenticate(*, authorization: str | None, settings: Settings) -> dict[str, Any]:
    if not settings.gate_watch_voice_actions_enabled:
        raise GitHubOidcError("watch_voice_actions_disabled")
    if re.fullmatch(r"[a-f0-9]{64}", settings.gate_watch_voice_actions_workflow_sha256) is None:
        raise GitHubOidcError("watch_voice_actions_pin_required")
    token = _bearer_token(authorization)
    if token is None or len(token) > 16_384:
        raise GitHubOidcError("watch_voice_oidc_token_required")
    try:
        claims = await asyncio.to_thread(_decode, token)
    except (InvalidTokenError, PyJWKClientError, OSError, ValueError):
        raise GitHubOidcError("watch_voice_oidc_invalid") from None
    authorize_claims(claims)
    return claims
