"""Content-free GitHub App sender port for Beta checkpoint wake-ups."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
import jwt

BETA_CHECKPOINT_DISPATCH_EVENT: Final = "beta-context-event-v1"
BETA_CHECKPOINT_DISPATCH_SCHEMA: Final = "beta.checkpoint-dispatch@1"
BETA_CHECKPOINT_REPOSITORY: Final = "AlphaCenter/beta"
GITHUB_API_ORIGIN: Final = "https://api.github.com"
MAX_GITHUB_RESPONSE_BYTES: Final = 32 * 1024
MAX_ACCEPTED_SEQUENCE: Final = 999_999_999_999

_SPACE_ID = re.compile(r"^spc_[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-.]{1,512}$")


class BetaCheckpointDispatchError(RuntimeError):
    """Fail closed with one stable content-free error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BetaCheckpointDispatchEnvelope:
    """Exact client payload accepted by the Beta repository receiver."""

    schema_version: str
    space_id: str
    accepted_sequence: int
    dispatch_id: str

    def as_client_payload(self) -> dict[str, str | int]:
        return {
            "schema_version": self.schema_version,
            "space_id": self.space_id,
            "accepted_sequence": self.accepted_sequence,
            "dispatch_id": self.dispatch_id,
        }


def build_beta_checkpoint_dispatch(
    *, space_id: str, accepted_sequence: int
) -> BetaCheckpointDispatchEnvelope:
    """Build a deterministic wake-up hint containing no Space content."""

    if _SPACE_ID.fullmatch(space_id) is None:
        raise BetaCheckpointDispatchError("beta_checkpoint_dispatch_space_invalid")
    if (
        type(accepted_sequence) is not int
        or accepted_sequence < 1
        or accepted_sequence > MAX_ACCEPTED_SEQUENCE
    ):
        raise BetaCheckpointDispatchError("beta_checkpoint_dispatch_sequence_invalid")
    digest = hashlib.sha256(
        (
            f"{BETA_CHECKPOINT_DISPATCH_SCHEMA}\n"
            f"{space_id}\n{accepted_sequence}"
        ).encode()
    ).hexdigest()
    return BetaCheckpointDispatchEnvelope(
        schema_version=BETA_CHECKPOINT_DISPATCH_SCHEMA,
        space_id=space_id,
        accepted_sequence=accepted_sequence,
        dispatch_id=f"dispatch-{digest}",
    )


class GitHubAppBetaCheckpointDispatcher:
    """Mint one short-lived installation token and send one exact dispatch."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        app_id: str,
        installation_id: str,
        private_key_pem: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not app_id.isdecimal()
            or not installation_id.isdecimal()
            or not private_key_pem.strip()
        ):
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_configuration_invalid"
            )
        self._client = client
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key_pem = private_key_pem
        self._now = now or (lambda: datetime.now(UTC))

    async def dispatch(
        self, *, space_id: str, accepted_sequence: int
    ) -> BetaCheckpointDispatchEnvelope:
        envelope = build_beta_checkpoint_dispatch(
            space_id=space_id,
            accepted_sequence=accepted_sequence,
        )
        installation_token = await self._mint_installation_token()
        try:
            response = await self._client.post(
                f"{GITHUB_API_ORIGIN}/repos/{BETA_CHECKPOINT_REPOSITORY}/dispatches",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {installation_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "event_type": BETA_CHECKPOINT_DISPATCH_EVENT,
                    "client_payload": envelope.as_client_payload(),
                },
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_unavailable"
            ) from exc
        if len(response.content) > MAX_GITHUB_RESPONSE_BYTES:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_response_oversized"
            )
        if response.status_code != 204:
            raise BetaCheckpointDispatchError("beta_checkpoint_dispatch_rejected")
        return envelope

    async def _mint_installation_token(self) -> str:
        observed_at = self._now().astimezone(UTC)
        try:
            app_jwt = jwt.encode(
                {
                    "iat": int((observed_at - timedelta(seconds=30)).timestamp()),
                    "exp": int((observed_at + timedelta(minutes=9)).timestamp()),
                    "iss": self._app_id,
                },
                self._private_key_pem,
                algorithm="RS256",
            )
        except (TypeError, ValueError) as exc:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_signing_invalid"
            ) from exc
        try:
            response = await self._client.post(
                (
                    f"{GITHUB_API_ORIGIN}/app/installations/"
                    f"{self._installation_id}/access_tokens"
                ),
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {app_jwt}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                content=b"",
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_token_unavailable"
            ) from exc
        if len(response.content) > MAX_GITHUB_RESPONSE_BYTES:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_token_response_oversized"
            )
        if response.status_code != 201:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_token_rejected"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_token_invalid"
            ) from exc
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise BetaCheckpointDispatchError(
                "beta_checkpoint_dispatch_token_invalid"
            )
        return token
