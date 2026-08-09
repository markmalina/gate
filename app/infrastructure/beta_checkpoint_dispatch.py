"""Content-free GitHub App sender port for Beta checkpoint wake-ups."""

from __future__ import annotations

import base64
import binascii
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
MAX_GITHUB_ARTIFACT_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_GITHUB_ARTIFACT_BYTES: Final = 5 * 1024 * 1024
MAX_ACCEPTED_SEQUENCE: Final = 999_999_999_999

_SPACE_ID = re.compile(r"^spc_[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-.]{1,512}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BetaCheckpointDispatchError(RuntimeError):
    """Fail closed with one stable content-free error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BetaCheckpointArtifactError(RuntimeError):
    """Fail closed when Git cannot prove the exact durable checkpoint blob."""

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
        try:
            return await _mint_installation_token(
                client=self._client,
                app_id=self._app_id,
                installation_id=self._installation_id,
                private_key_pem=self._private_key_pem,
                observed_at=self._now(),
                error_prefix="beta_checkpoint_dispatch",
            )
        except _GitHubAppTokenError as exc:
            raise BetaCheckpointDispatchError(exc.code) from exc


class GitHubAppBetaCheckpointArtifactVerifier:
    """Prove one pending ciphertext at its canonical reachable Git path."""

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
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_configuration_invalid"
            )
        self._client = client
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key_pem = private_key_pem
        self._now = now or (lambda: datetime.now(UTC))

    async def verify(
        self,
        *,
        space_id: str,
        sequence_start: int,
        sequence_end: int,
        ciphertext_sha256: str,
        github_commit_sha: str,
    ) -> None:
        if (
            _SPACE_ID.fullmatch(space_id) is None
            or type(sequence_start) is not int
            or type(sequence_end) is not int
            or sequence_start < 1
            or sequence_end < sequence_start
            or _SHA256.fullmatch(ciphertext_sha256) is None
            or _GIT_SHA.fullmatch(github_commit_sha) is None
        ):
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_scope_invalid"
            )
        try:
            token = await _mint_installation_token(
                client=self._client,
                app_id=self._app_id,
                installation_id=self._installation_id,
                private_key_pem=self._private_key_pem,
                observed_at=self._now(),
                error_prefix="beta_checkpoint_artifact",
            )
        except _GitHubAppTokenError as exc:
            raise BetaCheckpointArtifactError(exc.code) from exc
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        compare_path = (
            f"{GITHUB_API_ORIGIN}/repos/{BETA_CHECKPOINT_REPOSITORY}/compare/"
            f"{github_commit_sha}...main"
        )
        compare = await self._get(compare_path, headers=headers)
        compare_payload = _bounded_json(compare, MAX_GITHUB_RESPONSE_BYTES)
        if (
            compare.status_code != 200
            or not isinstance(compare_payload, dict)
            or compare_payload.get("status") not in {"ahead", "identical"}
        ):
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_commit_unreachable"
            )

        filename = (
            f"{sequence_start:012d}-{sequence_end:012d}.json.enc"
        )
        artifact_path = (
            f"spaces/{space_id}/context/checkpoints/{filename}"
        )
        contents_url = (
            f"{GITHUB_API_ORIGIN}/repos/{BETA_CHECKPOINT_REPOSITORY}/contents/"
            f"{artifact_path}?ref={github_commit_sha}"
        )
        artifact = await self._get(contents_url, headers=headers)
        payload = _bounded_json(
            artifact,
            MAX_GITHUB_ARTIFACT_RESPONSE_BYTES,
        )
        if (
            artifact.status_code != 200
            or not isinstance(payload, dict)
            or payload.get("type") != "file"
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_missing"
            )
        try:
            encoded_content = "".join(payload["content"].splitlines())
            content = base64.b64decode(encoded_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_invalid"
            ) from exc
        if (
            not content
            or len(content) > MAX_GITHUB_ARTIFACT_BYTES
            or hashlib.sha256(content).hexdigest() != ciphertext_sha256
        ):
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_hash_mismatch"
            )

    async def _get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        try:
            return await self._client.get(url, headers=headers, timeout=5.0)
        except httpx.HTTPError as exc:
            raise BetaCheckpointArtifactError(
                "beta_checkpoint_artifact_unavailable"
            ) from exc


class _GitHubAppTokenError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _mint_installation_token(
    *,
    client: httpx.AsyncClient,
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    observed_at: datetime,
    error_prefix: str,
) -> str:
    observed_at = observed_at.astimezone(UTC)
    try:
        app_jwt = jwt.encode(
            {
                "iat": int((observed_at - timedelta(seconds=30)).timestamp()),
                "exp": int((observed_at + timedelta(minutes=9)).timestamp()),
                "iss": app_id,
            },
            private_key_pem,
            algorithm="RS256",
        )
    except (TypeError, ValueError) as exc:
        raise _GitHubAppTokenError(f"{error_prefix}_signing_invalid") from exc
    try:
        response = await client.post(
            (
                f"{GITHUB_API_ORIGIN}/app/installations/"
                f"{installation_id}/access_tokens"
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
        raise _GitHubAppTokenError(
            f"{error_prefix}_token_unavailable"
        ) from exc
    if len(response.content) > MAX_GITHUB_RESPONSE_BYTES:
        raise _GitHubAppTokenError(
            f"{error_prefix}_token_response_oversized"
        )
    if response.status_code != 201:
        raise _GitHubAppTokenError(f"{error_prefix}_token_rejected")
    try:
        payload = response.json()
    except ValueError as exc:
        raise _GitHubAppTokenError(f"{error_prefix}_token_invalid") from exc
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise _GitHubAppTokenError(f"{error_prefix}_token_invalid")
    return token


def _bounded_json(response: httpx.Response, maximum_bytes: int) -> object:
    if len(response.content) > maximum_bytes:
        raise BetaCheckpointArtifactError(
            "beta_checkpoint_artifact_response_oversized"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise BetaCheckpointArtifactError(
            "beta_checkpoint_artifact_response_invalid"
        ) from exc
