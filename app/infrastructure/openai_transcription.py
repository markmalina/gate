"""Dormant OpenAI Audio Transcriptions adapter for normalized Telegram voice."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any

import httpx

from app.application.billing import UsageUnit
from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    ProviderTranscript,
    TranscriptionProviderError,
)
from app.infrastructure.openai_transcription_wif import (
    API_BASE_URL,
    transcription_workload_identity,
)

_SUPPORTED_MODELS = {
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
}
_logger = logging.getLogger(__name__)
_SAFE_DIAGNOSTIC_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")

_TOKEN_RATES_HUNDREDTHS_MICROUSD = {
    "gpt-4o-mini-transcribe": (125, 500),
    "gpt-4o-transcribe": (250, 1000),
}
WATCH_CANARY_MODEL = "gpt-transcribe"
WATCH_CANARY_MAX_SECONDS = 30
WATCH_BENCHMARK_MAX_SECONDS = 165
WATCH_BENCHMARK_SHA256 = "c70c430400cf443069a4495df3cfa2cdfc2682385892543208198bdf0dea5e44"
WATCH_CANARY_MAX_BYTES = 2_000_000
WATCH_CANARY_RESPONSE_MAX_BYTES = 262_144
WATCH_CANARY_MICROUSD_PER_SECOND = 75


@dataclass(frozen=True, slots=True)
class OpenAITranscriptionConfig:
    api_key: str = field(repr=False)
    enabled: bool = False
    model: str = "gpt-4o-mini-transcribe"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60
    watch_canary: bool = False
    watch_canary_max_seconds: int = WATCH_CANARY_MAX_SECONDS

    def __post_init__(self) -> None:
        if self.watch_canary:
            if (
                self.model != WATCH_CANARY_MODEL
                or self.base_url.rstrip("/") != API_BASE_URL
                or self.timeout_seconds != 60
                or self.watch_canary_max_seconds not in (
                    WATCH_CANARY_MAX_SECONDS, WATCH_BENCHMARK_MAX_SECONDS,
                )
            ):
                raise ValueError("openai_transcription_canary_config_invalid")
        elif self.model not in _SUPPORTED_MODELS:
            raise ValueError("openai_transcription_model_unsupported")
        if self.enabled and not self.api_key:
            raise ValueError("openai_transcription_api_key_required")
        if not self.base_url.startswith("https://"):
            raise ValueError("openai_transcription_base_url_invalid")
        if not 5 <= self.timeout_seconds <= 300:
            raise ValueError("openai_transcription_timeout_invalid")


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
        if self._config.watch_canary and (
            len(parts) != 1 or len(voice.data) > WATCH_CANARY_MAX_BYTES
        ):
            raise TranscriptionProviderError(
                "openai_transcription_canary_audio_out_of_scope", outcome_unknown=False
            )
        transcripts: list[ProviderTranscript] = []
        for index, part in enumerate(parts):
            prompt = (transcripts[-1].text[-1_000:] or None) if transcripts else None
            try:
                transcripts.append(
                    await self._transcribe_part(
                        part,
                        language=language,
                        prompt=prompt,
                    )
                )
                if len("\n".join(item.text for item in transcripts if item.text)) > 100_000:
                    raise TranscriptionProviderError(
                        "openai_transcription_response_unknown", outcome_unknown=True
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
            text="\n".join(item.text for item in transcripts if item.text),
            provider_operation_ref=(
                ";".join(operation_refs)[:255] if operation_refs else None
            ),
            billed_quantity=sum(item.billed_quantity for item in transcripts),
            billed_unit=transcripts[0].billed_unit,
            actual_microusd=sum(item.actual_microusd for item in transcripts),
            no_speech=all(item.no_speech for item in transcripts),
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
        identity = transcription_workload_identity(self._config.base_url)
        file = (voice.filename, voice.data, voice.media_type)
        try:
            if self._config.watch_canary:
                if identity is None:
                    raise TranscriptionProviderError(
                        "openai_transcription_workload_identity_unavailable", outcome_unknown=False
                    )
                # An uploaded recording has a single bounded response. No API-key fallback.
                response = await identity.post(
                    data=data, file=file, timeout=self._config.timeout_seconds,
                    max_response_bytes=WATCH_CANARY_RESPONSE_MAX_BYTES,
                )
            elif identity is None:
                response = await self._http.post(
                    f"{self._config.base_url.rstrip('/')}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    data=data,
                    files={"file": file},
                    timeout=self._config.timeout_seconds,
                )
            else:
                response = await identity.post(
                    data=data, file=file, timeout=self._config.timeout_seconds
                )
        except httpx.HTTPError:
            raise TranscriptionProviderError(
                "openai_transcription_transport_unknown",
                outcome_unknown=True,
            ) from None

        if response.is_error:
            _log_provider_error(response, model=self._config.model)
            raise TranscriptionProviderError(
                _provider_error_code(response),
                outcome_unknown=response.status_code >= 500,
            )
        if self._config.watch_canary and not response.is_success:
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown", outcome_unknown=True,
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
        if not isinstance(text, str) or len(text.strip()) > 100_000:
            raise TranscriptionProviderError(
                "openai_transcription_response_unknown",
                outcome_unknown=True,
            )
        usage = payload.get("usage")
        operation_ref = response.headers.get("x-request-id")
        if self._config.watch_canary:
            return _watch_canary_transcript(
                text.strip(), usage, operation_ref, self._config.watch_canary_max_seconds,
            )
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
            or (total_tokens == 0 and bool(text.strip()))
            or total_tokens != input_tokens + output_tokens
        ):
            raise TranscriptionProviderError(
                "openai_transcription_usage_unknown",
                outcome_unknown=True,
            )
        return ProviderTranscript(
            text=text.strip(),
            provider_operation_ref=operation_ref,
            billed_quantity=total_tokens,
            actual_microusd=_token_cost_microusd(
                self._config.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            no_speech=not text.strip(),
        )


def _watch_canary_transcript(
    text: str, usage: object, operation_ref: str | None, max_seconds: int,
) -> ProviderTranscript:
    raw = (
        usage.get("seconds")
        if isinstance(usage, dict) and usage.get("type") == "duration" else None
    )
    if type(raw) not in (int, float):
        raise TranscriptionProviderError("openai_transcription_usage_unknown", outcome_unknown=True)
    seconds = Decimal(str(raw))
    # Allow a one-second provider rounding margin over the archived duration gate.
    if not seconds.is_finite() or not 0 < seconds <= max_seconds + 1:
        raise TranscriptionProviderError("openai_transcription_usage_unknown", outcome_unknown=True)
    return ProviderTranscript(
        text=text,
        provider_operation_ref=operation_ref,
        billed_quantity=int(seconds.to_integral_value(rounding=ROUND_CEILING)),
        billed_unit=UsageUnit.SECONDS,
        actual_microusd=int(
            (seconds * WATCH_CANARY_MICROUSD_PER_SECOND).to_integral_value(rounding=ROUND_CEILING)
        ),
        no_speech=not text,
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


def _safe_diagnostic_token(value: Any) -> str:
    if isinstance(value, str) and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(value):
        return value
    return "absent"


def _log_provider_error(response: httpx.Response, *, model: str) -> None:
    """Log only support-safe provider metadata; never the provider message or payload."""

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    error_type = error.get("type") if isinstance(error, dict) else None
    error_code = error.get("code") if isinstance(error, dict) else None
    error_param = error.get("param") if isinstance(error, dict) else None
    _logger.warning(
        "openai_transcription_error status=%s model=%s request_id=%s type=%s code=%s param=%s",
        response.status_code,
        model,
        _safe_diagnostic_token(response.headers.get("x-request-id")),
        _safe_diagnostic_token(error_type),
        _safe_diagnostic_token(error_code),
        _safe_diagnostic_token(error_param),
    )


def _provider_error_code(response: httpx.Response) -> str:
    """Classify provider failures without persisting provider text or user content."""

    if response.status_code >= 500:
        return "openai_transcription_provider_unknown"
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if response.status_code == 429:
        return (
            "openai_transcription_quota_unavailable"
            if _quota_unavailable(error)
            else "openai_transcription_rate_limited"
        )
    message = error.get("message") if isinstance(error, dict) else None
    safe_fields = (
        message,
        error.get("code") if isinstance(error, dict) else None,
        error.get("type") if isinstance(error, dict) else None,
        error.get("param") if isinstance(error, dict) else None,
    )
    normalized = " ".join(
        value.lower() for value in safe_fields if isinstance(value, str)
    )
    if any(
        marker in normalized
        for marker in (
            "maximum context length",
            "context length",
            "context_length_exceeded",
            "input token",
            "too many token",
            "token limit",
        )
    ):
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
    elif (
        ("format" in normalized or "file type" in normalized)
        and (
            "unsupported" in normalized
            or "invalid" in normalized
            or "not supported" in normalized
        )
    ):
        category = "unsupported_format"
    elif any(
        marker in normalized
        for marker in (
            "invalid audio",
            "could not process",
            "failed to decode",
            "decode audio",
            "audio decode",
            "audio_decode",
            "invalid_audio",
        )
    ):
        category = "invalid_audio"
    else:
        category = f"http_{response.status_code}"
    return f"openai_transcription_rejected_{category}"


def _quota_unavailable(error: Any) -> bool:
    """Use typed fields only; never infer or persist a provider message."""
    if not isinstance(error, dict):
        return False
    return error.get("type") == "insufficient_quota" or error.get("code") in (
        "insufficient_quota",
        "organization_usage_limit_exceeded",
        "billing_hard_limit_reached",
        "billing_not_active",
        "credit_balance_too_low",
    )
