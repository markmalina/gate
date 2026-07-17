"""Compose Telegram voice transcription into a fail-closed downstream update."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.telegram_voice_transcription import (
    TelegramVoiceOutcome,
    TelegramVoiceTranscriptionResult,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
)
from app.application.telegram_voice_update import (
    extract_telegram_voice,
    project_transcribed_voice,
)

JsonObject = dict[str, Any]


class TelegramVoiceUpdateDisposition(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    PROJECTED = "projected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProcessTelegramVoiceUpdateCommand:
    update: JsonObject = field(repr=False)
    workspace_key: str
    connector_ref: str = field(repr=False)
    period_key: str
    requested_microusd: int
    occurred_at: datetime
    language: str | None = None
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class ProcessTelegramVoiceUpdateResult:
    disposition: TelegramVoiceUpdateDisposition
    reason_code: str
    downstream_update: JsonObject | None = field(default=None, repr=False)
    transcription: TelegramVoiceTranscriptionResult | None = field(
        default=None,
        repr=False,
    )
    downstream_allowed: bool = False

    def __post_init__(self) -> None:
        allowed_disposition = self.disposition in {
            TelegramVoiceUpdateDisposition.NOT_APPLICABLE,
            TelegramVoiceUpdateDisposition.PROJECTED,
        }
        if self.downstream_allowed != allowed_disposition:
            raise ValueError("telegram_voice_downstream_inconsistent")
        if self.downstream_allowed != (self.downstream_update is not None):
            raise ValueError("telegram_voice_downstream_payload_inconsistent")


class TelegramVoiceUpdateProcessor(Protocol):
    """Port used by the Telegram worker before canonical routing."""

    async def execute(
        self,
        command: ProcessTelegramVoiceUpdateCommand,
    ) -> ProcessTelegramVoiceUpdateResult: ...


class ProcessTelegramVoiceUpdateService:
    """Allow ordinary updates through; gate voice updates on text transcription."""

    def __init__(self, transcription: TelegramVoiceTranscriptionService) -> None:
        self._transcription = transcription

    async def execute(
        self,
        command: ProcessTelegramVoiceUpdateCommand,
    ) -> ProcessTelegramVoiceUpdateResult:
        envelope = extract_telegram_voice(command.update)
        if envelope is None:
            return ProcessTelegramVoiceUpdateResult(
                disposition=TelegramVoiceUpdateDisposition.NOT_APPLICABLE,
                reason_code="telegram_voice_not_applicable",
                downstream_update=command.update,
                downstream_allowed=True,
            )

        transcription = await self._transcription.transcribe(
            TranscribeTelegramVoiceCommand(
                voice=envelope.voice,
                workspace_key=command.workspace_key,
                connector_ref=command.connector_ref,
                reservation_key=envelope.source_ref,
                period_key=command.period_key,
                requested_microusd=command.requested_microusd,
                occurred_at=command.occurred_at,
                language=command.language,
                enabled=command.enabled,
            )
        )
        if transcription.outcome is TelegramVoiceOutcome.TRANSCRIBED:
            if transcription.alpha_input_text is None:
                raise ValueError("telegram_voice_transcript_missing")
            projected = project_transcribed_voice(
                command.update,
                envelope=envelope,
                transcript=transcription.alpha_input_text,
            )
            return ProcessTelegramVoiceUpdateResult(
                disposition=TelegramVoiceUpdateDisposition.PROJECTED,
                reason_code=transcription.reason_code,
                downstream_update=projected,
                transcription=transcription,
                downstream_allowed=True,
            )
        disposition = {
            TelegramVoiceOutcome.DISABLED: TelegramVoiceUpdateDisposition.DISABLED,
            TelegramVoiceOutcome.UNKNOWN: TelegramVoiceUpdateDisposition.UNKNOWN,
            TelegramVoiceOutcome.REJECTED: TelegramVoiceUpdateDisposition.BLOCKED,
        }[transcription.outcome]
        return ProcessTelegramVoiceUpdateResult(
            disposition=disposition,
            reason_code=transcription.reason_code,
            transcription=transcription,
        )
