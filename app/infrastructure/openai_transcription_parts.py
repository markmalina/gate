"""Single-attempt OpenAI transcription client for durable Alfa parts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.application.billing import UsageUnit
from app.application.telegram_voice_transcription import (
    ProviderTranscript,
    TranscriptionProviderError,
)

logger = logging.getLogger(__name__)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_RATES_HUNDREDTHS_MICROUSD = {
    "gpt-4o-mini-transcribe": (125, 500),
    "gpt-4o-transcribe": (250, 1000),
}


@dataclass(frozen=True, slots=True)
class OpenAITranscriptionPartConfig:
    api_key: str = field(repr=False)
    model: str = "gpt-4o-mini-transcribe"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("alpha_transcription_openai_key_required")
        if self.model not in _RATES_HUNDREDTHS_MICROUSD:
            raise ValueError("alpha_transcription_openai_model_invalid")
        if not self.base_url.startswith("https://"):
            raise ValueError("alpha_transcription_openai_url_invalid")
        if not 5 <= self.timeout_seconds <= 300:
            raise ValueError("alpha_transcription_openai_timeout_invalid")


class OpenAITranscriptionPartClient:
    def __init__(
        self,
        *,
        config: OpenAITranscriptionPartConfig,
        http: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._http = http

    async def transcribe_part(
        self,
        *,
        data: bytes,
        filename: str,
        language: str,
        continuity_prompt: str | None,
    ) -> ProviderTranscript:
        request: dict[str, str] = {
            "model": self._config.model,
            "response_format": "json",
            "language": language,
        }
        if continuity_prompt:
            request["prompt"] = continuity_prompt[-1_000:]
        try:
            response = await self._http.post(
                f"{self._config.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                data=request,
                files={"file": (filename, data, "audio/mpeg")},
                timeout=self._config.timeout_seconds,
            )
        except httpx.HTTPError:
            raise TranscriptionProviderError(
                "openai_transcription_transport_unknown",
                outcome_unknown=True,
            ) from None
        if response.is_error:
            _log_error(response, model=self._config.model)
            raise TranscriptionProviderError(
                _error_code(response),
                outcome_unknown=response.status_code >= 500,
            )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            )
        text = payload.get("text")
        usage = payload.get("usage")
        if not isinstance(text, str) or not text.strip() or not isinstance(usage, dict):
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            )
        if usage.get("type") != "tokens":
            raise TranscriptionProviderError(
                "openai_transcription_usage_unknown",
                outcome_unknown=True,
            )
        input_tokens = _count(usage.get("input_tokens"))
        output_tokens = _count(usage.get("output_tokens"))
        total_tokens = _count(usage.get("total_tokens"))
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
        return ProviderTranscript(
            text=text.strip(),
            provider_operation_ref=response.headers.get("x-request-id"),
            billed_quantity=total_tokens,
            billed_unit=UsageUnit.TOKENS,
            actual_microusd=_cost(
                self._config.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _cost(model: str, *, input_tokens: int, output_tokens: int) -> int:
    input_rate, output_rate = _RATES_HUNDREDTHS_MICROUSD[model]
    return (input_tokens * input_rate + output_tokens * output_rate + 99) // 100


def _safe(value: Any) -> str:
    if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
        return value
    return "absent"


def _log_error(response: httpx.Response, *, model: str) -> None:
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    logger.warning(
        "alpha_transcription_provider_error status=%s model=%s request_id=%s "
        "type=%s code=%s param=%s",
        response.status_code,
        model,
        _safe(response.headers.get("x-request-id")),
        _safe(error.get("type") if isinstance(error, dict) else None),
        _safe(error.get("code") if isinstance(error, dict) else None),
        _safe(error.get("param") if isinstance(error, dict) else None),
    )


def _error_code(response: httpx.Response) -> str:
    if response.status_code >= 500:
        return "openai_transcription_provider_unknown"
    if response.status_code == 429:
        return "openai_transcription_rate_limited"
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    fields = (
        error.get("message") if isinstance(error, dict) else None,
        error.get("code") if isinstance(error, dict) else None,
        error.get("type") if isinstance(error, dict) else None,
        error.get("param") if isinstance(error, dict) else None,
    )
    normalized = " ".join(item.casefold() for item in fields if isinstance(item, str))
    if "context" in normalized and "limit" in normalized:
        category = "context_limit"
    elif "duration" in normalized and (
        "limit" in normalized or "maximum" in normalized
    ):
        category = "duration_limit"
    elif "file size" in normalized or (
        "maximum" in normalized and "size" in normalized
    ):
        category = "file_size_limit"
    elif "format" in normalized and (
        "unsupported" in normalized or "invalid" in normalized
    ):
        category = "unsupported_format"
    elif "audio" in normalized and (
        "decode" in normalized or "invalid" in normalized
    ):
        category = "invalid_audio"
    else:
        category = f"http_{response.status_code}"
    return f"openai_transcription_rejected_{category}"
