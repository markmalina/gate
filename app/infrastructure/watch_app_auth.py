"""Issue a short-lived, repository-scoped token for the existing GitHub App.

This module has no scheduling or publishing side effects. Callers must hold the
private key securely, verify their target, and never log tokens or exceptions.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

API = "https://api.github.com"
APP_ID = 5641721
INSTALLATION_ID = 179245428
OWNER_ID = 23397294
REPO_ID = 1336157811
REPO = "markmalina/mark"
APP_SLUG = "markmalina-n8n-upload"


class AppAuthError(Exception):
    """A deliberately non-sensitive authentication failure."""


async def _json(response: httpx.Response, expected_status: int) -> dict[str, Any]:
    if response.status_code != expected_status or len(response.content) > 65536:
        raise AppAuthError("github_app_request_failed")
    try:
        value = response.json()
    except ValueError:
        raise AppAuthError("github_app_response_invalid") from None
    if not isinstance(value, dict):
        raise AppAuthError("github_app_response_invalid")
    return value


async def issue_mark_token(http: httpx.AsyncClient, private_key: str) -> str:
    """Issue only Issues read/write for Mark, after checking app and install IDs."""
    if not private_key or len(private_key) > 16384:
        raise AppAuthError("github_app_key_invalid")
    now = datetime.now(UTC)
    try:
        assertion = jwt.encode(
            {
                "iat": int((now - timedelta(seconds=30)).timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iss": str(APP_ID),
            },
            private_key,
            algorithm="RS256",
        )
        headers = {
            "Authorization": f"Bearer {assertion}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        install = await _json(
            await http.get(
                f"{API}/app/installations/{INSTALLATION_ID}",
                headers=headers,
                follow_redirects=False,
                timeout=15.0,
            ),
            200,
        )
        if (
            install.get("id") != INSTALLATION_ID
            or install.get("app_id") != APP_ID
            or install.get("app_slug") != APP_SLUG
            or not isinstance(install.get("account"), dict)
            or install["account"].get("id") != OWNER_ID
            or install.get("suspended_at") is not None
        ):
            raise AppAuthError("github_app_identity_mismatch")
        payload = {"repository_ids": [REPO_ID], "permissions": {"issues": "write"}}
        result = await _json(
            await http.post(
                f"{API}/app/installations/{INSTALLATION_ID}/access_tokens",
                headers=headers,
                json=payload,
                follow_redirects=False,
                timeout=15.0,
            ),
            201,
        )
        token, permissions = result.get("token"), result.get("permissions")
        if (
            not isinstance(token, str)
            or not 20 <= len(token) <= 4096
            or "\n" in token
            or "\r" in token
            or not isinstance(permissions, dict)
            or permissions.get("issues") != "write"
            or any(k not in {"issues", "metadata"} for k in permissions)
        ):
            raise AppAuthError("github_app_token_invalid")
        repo = await _json(
            await http.get(
                f"{API}/repos/{REPO}",
                headers={**headers, "Authorization": f"Bearer {token}"},
                follow_redirects=False,
                timeout=15.0,
            ),
            200,
        )
        if (
            repo.get("id") != REPO_ID
            or repo.get("private") is not True
            or repo.get("archived") is not False
            or repo.get("has_issues") is not True
            or not isinstance(repo.get("owner"), dict)
            or repo["owner"].get("id") != OWNER_ID
        ):
            raise AppAuthError("github_app_target_mismatch")
        return token
    except AppAuthError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError):
        raise AppAuthError("github_app_auth_failed") from None
