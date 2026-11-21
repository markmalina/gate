"""Certificate-backed credentials for OpenAI's transcription endpoint."""

from __future__ import annotations

import asyncio
import os
import re
import ssl
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.application.telegram_voice_transcription import TranscriptionProviderError

API_BASE_URL = "https://api.openai.com/v1"
MTLS_TRANSCRIPTIONS = "https://mtls.api.openai.com/v1/audio/transcriptions"
TOKEN_EXCHANGE = "https://mtls.auth.openai.com/oauth/token"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*")


class TranscriptionWorkloadIdentity:
    """Exchange the client certificate without sending audio or logging credentials."""

    def __init__(self, context: ssl.SSLContext, provider_id: str, account_id: str) -> None:
        self._context = context
        self._provider_id = provider_id
        self._account_id = account_id
        self._lock = asyncio.Lock()
        self._token = ""
        self._refresh_at = 0.0

    async def post(
        self,
        *,
        data: dict[str, str],
        file: tuple[str, bytes, str],
        timeout: float,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            verify=self._context, trust_env=False, follow_redirects=False
        ) as http:
            started_at = time.monotonic()
            token = await self._authorization(http)
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                raise _identity_error()
            try:
                async with asyncio.timeout(remaining):
                    return await http.post(
                        MTLS_TRANSCRIPTIONS,
                        headers={"Authorization": f"Bearer {token}"},
                        data=data,
                        files={"file": file},
                        timeout=remaining,
                    )
            except TimeoutError:
                raise httpx.ReadTimeout("Transcription request timed out") from None

    async def _authorization(self, http: httpx.AsyncClient) -> str:
        async with self._lock:
            if time.monotonic() < self._refresh_at:
                return self._token
            try:
                async with asyncio.timeout(10):
                    response = await http.post(
                        TOKEN_EXCHANGE,
                        json={
                            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                            "subject_token_type": "urn:openai:params:oauth:token-type:x509",
                            "identity_provider_id": self._provider_id,
                            "service_account_id": self._account_id,
                        },
                        timeout=10,
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, TimeoutError, ValueError):
                raise _identity_error() from None

            if not isinstance(payload, dict):
                raise _identity_error()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(token, str)
                or len(token) > 8192
                or not _TOKEN_PATTERN.fullmatch(token)
                or type(expires_in) is not int
                or not 0 < expires_in <= 3600
                or payload.get("token_type") != "Bearer"
            ):
                raise _identity_error()
            self._token = token
            self._refresh_at = time.monotonic() + max(0, expires_in - 30)
            return token


_identity: TranscriptionWorkloadIdentity | None = None


def transcription_workload_identity(base_url: str) -> TranscriptionWorkloadIdentity | None:
    """Read the opt-in once credentials are needed; configured identity fails closed."""
    enabled = os.environ.get("OPENAI_TRANSCRIPTION_WIF_ENABLED", "false").casefold()
    if enabled == "false":
        return None
    if enabled != "true" or base_url.rstrip("/") != API_BASE_URL:
        raise _identity_error()

    global _identity
    if _identity is None:
        provider_id = os.environ.get("OPENAI_TRANSCRIPTION_WIF_PROVIDER_ID", "")
        account_id = os.environ.get("OPENAI_TRANSCRIPTION_WIF_SERVICE_ACCOUNT_ID", "")
        certificate = os.environ.get("OPENAI_TRANSCRIPTION_WIF_CERT_PEM", "")
        private_key = os.environ.get("OPENAI_TRANSCRIPTION_WIF_KEY_PEM", "")
        if not all((provider_id, account_id, certificate, private_key)):
            raise _identity_error()
        try:
            context = ssl.create_default_context()
            with TemporaryDirectory(prefix="gate-transcription-wif-") as directory:
                cert_path = Path(directory) / "client.pem"
                key_path = Path(directory) / "key.pem"
                cert_path.write_text(certificate, encoding="ascii")
                key_path.write_text(private_key, encoding="ascii")
                context.load_cert_chain(cert_path, key_path)
        except (OSError, UnicodeError, ValueError):
            raise _identity_error() from None
        _identity = TranscriptionWorkloadIdentity(context, provider_id, account_id)
    return _identity


def _identity_error() -> TranscriptionProviderError:
    return TranscriptionProviderError(
        "openai_transcription_workload_identity_unavailable", outcome_unknown=False
    )
