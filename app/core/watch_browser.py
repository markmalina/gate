"""Browser configuration for the private Watch archive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.config import Settings

CALLBACK_PATH = "/watch/oauth/callback"


def watch_path(event_id: str) -> str:
    return f"/watch/records/{event_id}"


@dataclass(frozen=True)
class WatchBrowser:
    origin: str
    issuer: str
    audience: str
    client_id: str
    owner_subject: str

    @property
    def callback(self) -> str:
        return self.origin + CALLBACK_PATH


def watch_browser(settings: Settings) -> WatchBrowser | None:
    client = getattr(settings, "gate_watch_browser_client_id", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client):
        return None
    resource = settings.mcp_public_resource_url.strip().rstrip("/")
    issuer = settings.mcp_authorization_server.strip()
    owner = settings.gate_watch_browser_owner_subject.strip()
    if (
        settings.gate_workspace_id != "mark-personal"
        or not settings.dropbox_call_account_id
        or not settings.gate_mcp_enabled
        or settings.mcp_auth_mode != "oauth"
        or not settings.mcp_oauth_jwks_url
        or not owner
        or not resource.endswith("/mcp")
        or not issuer.endswith("/")
    ):
        return None

    origin = resource.removesuffix("/mcp")
    for value in (origin, issuer[:-1]):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        if (
            parts.scheme != "https"
            or not parts.netloc
            or value != f"https://{parts.netloc}"
            or not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", parts.netloc)
        ):
            return None
    return WatchBrowser(origin, issuer, resource, client, owner)
