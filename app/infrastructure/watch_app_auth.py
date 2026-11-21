"""Issue a short-lived token for Watch's private Mark repository.

This module has no scheduling or publishing side effects. Callers must hold the
private key securely, verify their target, and never log tokens or exceptions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

API = "https://api.github.com"
OWNER_ID = 23397294
REPO_ID = 1336157811
REPO = "markmalina/mark"


@dataclass(frozen=True)
class WatchAppInstallation:
    app_id: int
    installation_id: int
    slug: str


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


async def issue_mark_token(
    http: httpx.AsyncClient, private_key: str, identity: WatchAppInstallation
) -> str:
    """Issue only Issues read/write for Mark, after checking app and install IDs."""
    if not private_key or len(private_key) > 16384:
        raise AppAuthError("github_app_key_invalid")
    now = datetime.now(UTC)
    try:
        assertion = jwt.encode(
            {
                "iat": int((now - timedelta(seconds=30)).timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iss": str(identity.app_id),
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
                f"{API}/app/installations/{identity.installation_id}",
                headers=headers,
                follow_redirects=False,
                timeout=15.0,
            ),
            200,
        )
        installation_permissions = install.get("permissions")
        if (
            install.get("id") != identity.installation_id
            or install.get("app_id") != identity.app_id
            or install.get("app_slug") != identity.slug
            or not isinstance(install.get("account"), dict)
            or install["account"].get("id") != OWNER_ID
            or install.get("suspended_at") is not None
            or install.get("repository_selection") not in ("all", "selected")
            or not isinstance(installation_permissions, dict)
            or installation_permissions.get("issues") != "write"
        ):
            raise AppAuthError("github_app_identity_mismatch")
        payload = {"repository_ids": [REPO_ID], "permissions": {"issues": "write"}}
        result = await _json(
            await http.post(
                f"{API}/app/installations/{identity.installation_id}/access_tokens",
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
            or permissions.get("metadata", "read") != "read"
            or any(k not in {"issues", "metadata"} for k in permissions)
        ):
            raise AppAuthError("github_app_token_invalid")
        accessible = await _json(
            await http.get(
                f"{API}/installation/repositories",
                params={"per_page": 2},
                headers={**headers, "Authorization": f"Bearer {token}"},
                follow_redirects=False,
                timeout=15.0,
            ),
            200,
        )
        repositories = accessible.get("repositories")
        if (
            accessible.get("total_count") != 1
            or not isinstance(repositories, list)
            or len(repositories) != 1
            or not isinstance(repositories[0], dict)
        ):
            raise AppAuthError("github_app_token_scope_mismatch")
        repo = repositories[0]
        if (
            repo.get("id") != REPO_ID
            or repo.get("full_name") != REPO
            or repo.get("private") is not True
            or not isinstance(repo.get("owner"), dict)
            or repo["owner"].get("id") != OWNER_ID
        ):
            raise AppAuthError("github_app_target_mismatch")
        return token
    except AppAuthError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError):
        raise AppAuthError("github_app_auth_failed") from None
