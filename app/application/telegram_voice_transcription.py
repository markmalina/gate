"""Budgeted Telegram voice-to-text boundary owned by Gate, not Alpha."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.billing import (
    BillingProduct,
    BudgetAdmission,
    BudgetReservation,
    ReserveBudgetCommand,
    UsageAttribution,
    UsageOperation,
    UsageUnit,
)

# The provider documents a decimal 25 MB request limit.
OPENAI_TRANSCRIPTION_LIMIT_BYTES = 25_000_000
# Gate may archive a slightly larger bounded source and compress it before upload.
TELEGRAM_DOWNLOAD_LIMIT_BYTES = 25 * 1024 * 1024


class TelegramVoiceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TranscriptionProviderError(RuntimeError):
    """Sanitized provider failure with explicit request-outcome certainty."""

    def __init__(self, code: str, *, outcome_unknown: bool) -> None:
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


class TelegramVoiceOutcome(StrEnum):
    DISABLED = "disabled"
    REJECTED = "rejected"
    TRANSCRIBED = "transcribed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TelegramVoiceReference:
    file_id: str = field(repr=False)
    file_unique_id: str = field(repr=False)
    declared_size_bytes: int | None
    duration_seconds: int

    def __post_init__(self) -> None:
        _token(self.file_id, "telegram_voice_file_id_invalid", 512)
        _token(self.file_unique_id, "telegram_voice_unique_id_invalid", 512)
        if self.declared_size_bytes is not None and self.declared_size_bytes <= 0:
            raise TelegramVoiceError("telegram_voice_size_invalid")
        if self.duration_seconds <= 0:
            raise TelegramVoiceError("telegram_voice_duration_invalid")


@dataclass(frozen=True, slots=True)
class DownloadedVoice:
    data: bytes = field(repr=False)
    filename: str
    media_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise TelegramVoiceError("telegram_voice_empty")
        _token(self.filename, "telegram_voice_filename_invalid", 255)
        _token(self.media_type, "telegram_voice_media_type_invalid", 128)


@dataclass(frozen=True, slots=True)
class NormalizedVoice:
    data: bytes = field(repr=False)
    filename: str
    media_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise TelegramVoiceError("telegram_voice_normalized_empty")
        if len(self.data) > OPENAI_TRANSCRIPTION_LIMIT_BYTES:
            raise TelegramVoiceError("telegram_voice_normalized_too_large")
        _token(self.filename, "telegram_voice_filename_invalid", 255)
        _token(self.media_type, "telegram_voice_media_type_invalid", 128)


@dataclass(frozen=True, slots=True)
class ProviderTranscript:
    text: str
    provider_operation_ref: str | None = field(default=None, repr=False)
    billed_quantity: int = 0
    billed_unit: UsageUnit = UsageUnit.TOKENS
    actual_microusd: int = 0

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise TelegramVoiceError("telegram_voice_transcript_empty")
        if self.text != self.text.strip():
            raise TelegramVoiceError("telegram_voice_transcript_not_normalized")
        if len(self.text) > 100_000:
            raise TelegramVoiceError("telegram_voice_transcript_too_large")
        if self.provider_operation_ref is not None:
            _token(
                self.provider_operation_ref,
                "telegram_voice_provider_ref_invalid",
                255,
            )
        if self.billed_quantity < 0 or self.actual_microusd < 0:
            raise TelegramVoiceError("telegram_voice_usage_invalid")


@dataclass(frozen=True, slots=True)
class TranscribeTelegramVoiceCommand:
    voice: TelegramVoiceReference
    workspace_key: str
    connector_ref: str = field(repr=False)
    reservation_key: str = field(repr=False)
    period_key: str
    requested_microusd: int
    occurred_at: datetime
    language: str | None = None
    enabled: bool = False
    budget_scope_kind: str = "telegram_connector"

    def __post_init__(self) -> None:
        _token(self.workspace_key, "telegram_voice_workspace_invalid", 128)
        _token(self.connector_ref, "telegram_voice_connector_invalid", 255)
        _token(self.reservation_key, "telegram_voice_reservation_invalid", 128)
        _token(self.period_key, "telegram_voice_period_invalid", 64)
        if self.requested_microusd <= 0:
            raise TelegramVoiceError("telegram_voice_reservation_amount_invalid")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise TelegramVoiceError("telegram_voice_time_invalid")
        if self.language is not None:
            _token(self.language, "telegram_voice_language_invalid", 32)
        _token(
            self.budget_scope_kind,
            "telegram_voice_budget_scope_invalid",
            64,
        )


@dataclass(frozen=True, slots=True)
class TelegramVoiceTranscriptionResult:
    outcome: TelegramVoiceOutcome
    reason_code: str
    alpha_input_text: str | None = None
    budget: BudgetAdmission | None = field(default=None, repr=False)
    route_to_alpha_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        transcribed = self.outcome is TelegramVoiceOutcome.TRANSCRIBED
        if self.route_to_alpha_allowed != transcribed:
            raise TelegramVoiceError("telegram_voice_route_inconsistent")
        if transcribed != (self.alpha_input_text is not None):
            raise TelegramVoiceError("telegram_voice_text_inconsistent")
        if transcribed and (self.budget is None or not self.budget.allowed):
            raise TelegramVoiceError("telegram_voice_budget_inconsistent")
        if self.telegram_delivery_allowed:
            raise TelegramVoiceError("telegram_voice_cannot_grant_delivery")


class TelegramVoiceDownloadPort(Protocol):
    async def download(self, voice: TelegramVoiceReference) -> DownloadedVoice: ...


class TelegramVoiceNormalizerPort(Protocol):
    async def normalize(self, voice: DownloadedVoice) -> NormalizedVoice: ...


class TranscriptionBudgetPort(Protocol):
    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission: ...

    async def settle(
        self,
        reservation: BudgetReservation,
        *,
        actual_microusd: int,
        quantity: int,
        unit: UsageUnit,
        provider_operation_ref: str | None,
        occurred_at: datetime,
    ) -> None: ...

    async def release(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
    ) -> None: ...

    async def mark_unknown(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
        error_code: str,
    ) -> None: ...


class SpeechToTextPort(Protocol):
    @property
    def ready(self) -> bool: ...

    async def transcribe(
        self,
        voice: NormalizedVoice,
        *,
        language: str | None,
    ) -> ProviderTranscript: ...


class TelegramVoiceTranscriptionService:
    """Download and transcribe once, before any Alpha routing or reply delivery."""

    def __init__(
        self,
        *,
        downloader: TelegramVoiceDownloadPort,
        normalizer: TelegramVoiceNormalizerPort,
        budget: TranscriptionBudgetPort,
        speech_to_text: SpeechToTextPort,
    ) -> None:
        self._downloader = downloader
        self._normalizer = normalizer
        self._budget = budget
        self._speech_to_text = speech_to_text

    async def transcribe(
        self,
        command: TranscribeTelegramVoiceCommand,
    ) -> TelegramVoiceTranscriptionResult:
        if not command.enabled or not self._speech_to_text.ready:
            return TelegramVoiceTranscriptionResult(
                outcome=TelegramVoiceOutcome.DISABLED,
                reason_code="telegram_voice_transcription_disabled",
            )
        if (
            command.voice.declared_size_bytes is not None
            and command.voice.declared_size_bytes > TELEGRAM_DOWNLOAD_LIMIT_BYTES
        ):
            return _rejected("telegram_voice_download_limit_exceeded")

        try:
            downloaded = await self._downloader.download(command.voice)
            if len(downloaded.data) > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
                return _rejected("telegram_voice_download_limit_exceeded")
            normalized = await self._normalizer.normalize(downloaded)
        except TelegramVoiceError as exc:
            return _rejected(exc.code)

        budget = await self._budget.reserve(
            ReserveBudgetCommand(
                reservation_key=command.reservation_key,
                attribution=UsageAttribution(
                    workspace_key=command.workspace_key,
                    product=BillingProduct.GATE,
                    scope_kind=command.budget_scope_kind,
                    scope_ref=command.connector_ref,
                ),
                operation=UsageOperation.TRANSCRIPTION,
                requested_microusd=command.requested_microusd,
                occurred_at=command.occurred_at,
            ),
            period_key=command.period_key,
        )
        if not budget.allowed:
            return _rejected(budget.reason_code, budget=budget)
        reservation = budget.reservation
        if reservation is None:
            raise TelegramVoiceError("telegram_voice_budget_reservation_missing")

        try:
            transcript = await self._speech_to_text.transcribe(
                normalized,
                language=command.language,
            )
        except TranscriptionProviderError as exc:
            if exc.outcome_unknown:
                await self._budget.mark_unknown(
                    reservation,
                    occurred_at=command.occurred_at,
                    error_code=exc.code,
                )
            else:
                await self._budget.release(
                    reservation,
                    occurred_at=command.occurred_at,
                )
            return TelegramVoiceTranscriptionResult(
                outcome=(
                    TelegramVoiceOutcome.UNKNOWN
                    if exc.outcome_unknown
                    else TelegramVoiceOutcome.REJECTED
                ),
                reason_code=exc.code,
                budget=budget,
            )

        await self._budget.settle(
            reservation,
            actual_microusd=transcript.actual_microusd,
            quantity=transcript.billed_quantity,
            unit=transcript.billed_unit,
            provider_operation_ref=transcript.provider_operation_ref,
            occurred_at=command.occurred_at,
        )
        return TelegramVoiceTranscriptionResult(
            outcome=TelegramVoiceOutcome.TRANSCRIBED,
            reason_code="telegram_voice_transcribed",
            alpha_input_text=transcript.text,
            budget=budget,
            route_to_alpha_allowed=True,
        )


def _rejected(
    reason_code: str,
    *,
    budget: BudgetAdmission | None = None,
) -> TelegramVoiceTranscriptionResult:
    return TelegramVoiceTranscriptionResult(
        outcome=TelegramVoiceOutcome.REJECTED,
        reason_code=reason_code,
        budget=budget,
    )


def _token(value: str, code: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise TelegramVoiceError(code)
