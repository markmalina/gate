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
_TOKEN_RATES_HUNDREDTHS_MICROUSD = {
    "gpt-4o-mini-transcribe": (125, 500),
    "gpt-4o-transcribe": (250, 1000),
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

        parts = (voice, *voice.additional_parts)
        transcripts: list[ProviderTranscript] = []
        for index, part in enumerate(parts):
            prompt = transcripts[-1].text[-1_000:] if transcripts else None
            try:
                transcripts.append(
                    await self._transcribe_part(
                        part,
                        language=language,
                        prompt=prompt,
                    )
                )
            except TranscriptionProviderError as exc:
                raise TranscriptionProviderError(
                    exc.code,
                    outcome_unknown=exc.outcome_unknown or index > 0,
                ) from None

        operation_refs = [
            item.provider_operation_ref
            for item in transcripts
            if item.provider_operation_ref is not None
        ]
        return ProviderTranscript(
            text="\n".join(item.text for item in transcripts),
            provider_operation_ref=(
                ";".join(operation_refs)[:255] if operation_refs else None
            ),
            billed_quantity=sum(item.billed_quantity for item in transcripts),
            actual_microusd=sum(item.actual_microusd for item in transcripts),
        )

    async def _transcribe_part(
        self,
        voice: NormalizedVoice,
        *,
        language: str | None,
        prompt: str | None,
    ) -> ProviderTranscript:
        data = {"model": self._config.model, "response_format": "json"}
        if language is not None:
            data["language"] = language
        if prompt is not None:
            data["prompt"] = prompt
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
                _provider_error_code(response),
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
        usage = payload.get("usage")
        if not isinstance(usage, dict) or usage.get("type") != "tokens":
            raise TranscriptionProviderError(
                "openai_transcription_usage_unknown",
                outcome_unknown=True,
            )
        input_tokens = _usage_count(usage.get("input_tokens"))
        output_tokens = _usage_count(usage.get("output_tokens"))
        total_tokens = _usage_count(usage.get("total_tokens"))
        if (
            input_tokens is None
            or output_tokens is None
            or total_tokens is None
            or total_tokens <= 0
            or total_tokens != input_tokens + output_tokens
        ):
            raise TranscriptionProviderError(
                "openai_transcription_usage_unknown",
                outcome_unknown=True,
            )
        operation_ref = response.headers.get("x-request-id")
        return ProviderTranscript(
            text=text.strip(),
            provider_operation_ref=operation_ref,
            billed_quantity=total_tokens,
            actual_microusd=_token_cost_microusd(
                self._config.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )

def _usage_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _token_cost_microusd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> int:
    input_rate, output_rate = _TOKEN_RATES_HUNDREDTHS_MICROUSD[model]
    hundredths = input_tokens * input_rate + output_tokens * output_rate
    return (hundredths + 99) // 100


def _provider_error_code(response: httpx.Response) -> str:
    """Classify provider failures without persisting provider text or user content."""

    if response.status_code >= 500:
        return "openai_transcription_provider_unknown"
    if response.status_code == 429:
        return "openai_transcription_rate_limited"
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    normalized = message.lower() if isinstance(message, str) else ""
    if "maximum context length" in normalized or "context length" in normalized:
        category = "context_limit"
    elif "duration" in normalized and (
        "limit" in normalized or "long" in normalized or "maximum" in normalized
    ):
        category = "duration_limit"
    elif (
        "file size" in normalized
        or ("larger than" in normalized and "mb" in normalized)
        or ("maximum" in normalized and "size" in normalized)
    ):
        category = "file_size_limit"
    elif "format" in normalized and (
        "unsupported" in normalized or "invalid" in normalized
    ):
        category = "unsupported_format"
    elif "invalid audio" in normalized or "could not process" in normalized:
        category = "invalid_audio"
    else:
        category = f"http_{response.status_code}"
    return f"openai_transcription_rejected_{category}"
