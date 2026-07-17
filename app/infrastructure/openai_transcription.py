"""Dormant OpenAI Audio Transcriptions adapter for normalized Telegram voice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    ProviderTranscript,
    TranscriptionProviderError,
)

_SUPPORTED_MODELS = {
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
}


@dataclass(frozen=True, slots=True)
class OpenAITranscriptionConfig:
    api_key: str = field(repr=False)
    enabled: bool = False
    model: str = "gpt-4o-mini-transcribe"
    base_url: str = "https://api.openai.com/v1"

    def __post_init__(self) -> None:
        if self.model not in _SUPPORTED_MODELS:
            raise ValueError("openai_transcription_model_unsupported")
        if self.enabled and not self.api_key:
            raise ValueError("openai_transcription_api_key_required")
        if not self.base_url.startswith("https://"):
            raise ValueError("openai_transcription_base_url_invalid")


class OpenAITranscriptionClient:
    """Call the bounded file transcription endpoint, never Alpha or delivery APIs."""

    def __init__(
        self,
        *,
        config: OpenAITranscriptionConfig,
        http: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._http = http

    @property
    def ready(self) -> bool:
        return self._config.enabled and bool(self._config.api_key)

    async def transcribe(
        self,
        voice: NormalizedVoice,
        *,
        language: str | None,
    ) -> ProviderTranscript:
        if not self._config.enabled:
            raise TranscriptionProviderError(
                "openai_transcription_disabled",
                outcome_unknown=False,
            )

        data = {"model": self._config.model, "response_format": "json"}
        if language is not None:
            data["language"] = language
        try:
            response = await self._http.post(
                f"{self._config.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                data=data,
                files={"file": (voice.filename, voice.data, voice.media_type)},
            )
        except httpx.HTTPError:
            raise TranscriptionProviderError(
                "openai_transcription_transport_unknown",
                outcome_unknown=True,
            ) from None

        if response.is_error:
            raise TranscriptionProviderError(
                "openai_transcription_rejected"
                if response.status_code < 500
                else "openai_transcription_provider_unknown",
                outcome_unknown=response.status_code >= 500,
            )
        try:
            payload: Any = response.json()
        except ValueError:
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            ) from None
        if not isinstance(payload, dict):
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            )
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            )
        operation_ref = response.headers.get("x-request-id")
        return ProviderTranscript(
            text=text.strip(),
            provider_operation_ref=operation_ref,
        )
