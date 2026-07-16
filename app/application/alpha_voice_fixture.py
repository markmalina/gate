"""Delivery-blocked end-to-end fixture runner for Alpha Telegram voice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256

from app.application.alpha_voice import (
    AlphaVoiceDomainError,
    AlphaVoiceResponse,
    DeterministicFixtureTranscriptionPort,
    TelegramVoiceMetadata,
    TranscriptionJobState,
    VoiceTranscriptionJob,
    VoiceTranscriptionRequest,
    build_alpha_voice_response,
    settle_transcription_job,
    start_transcription_job,
)


@dataclass(frozen=True, slots=True)
class RunAlphaVoiceFixtureCommand:
    queued_job: VoiceTranscriptionJob = field(repr=False)
    metadata: TelegramVoiceMetadata = field(repr=False)
    audio_bytes: bytes = field(repr=False)
    started_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        if self.queued_job.state is not TranscriptionJobState.QUEUED:
            raise AlphaVoiceDomainError("voice_fixture_job_not_queued")
        if self.completed_at < self.started_at:
            raise AlphaVoiceDomainError("voice_fixture_time_regression")


@dataclass(frozen=True, slots=True)
class AlphaVoiceFixtureResult:
    completed_job: VoiceTranscriptionJob = field(repr=False)
    response: AlphaVoiceResponse = field(repr=False)
    operation_ref: str = field(repr=False)
    telegram_delivery_blocked: bool = True
    provider_network_used: bool = False

    def __post_init__(self) -> None:
        if self.completed_job.state is not TranscriptionJobState.COMPLETED:
            raise AlphaVoiceDomainError("voice_fixture_job_not_completed")
        if self.telegram_delivery_blocked is not True:
            raise AlphaVoiceDomainError("voice_fixture_delivery_must_be_blocked")
        if self.provider_network_used is not False:
            raise AlphaVoiceDomainError("voice_fixture_network_must_be_disabled")

    def render(self) -> str:
        return self.response.render()


class DeterministicAlphaVoiceFixtureRunner:
    """Run the complete product contract without Telegram, files or a provider."""

    def __init__(
        self,
        *,
        transcript: str,
        understanding: str,
        answer: str,
    ) -> None:
        self._transcription = DeterministicFixtureTranscriptionPort(transcript)
        self._understanding = understanding
        self._answer = answer

    def __repr__(self) -> str:
        return "DeterministicAlphaVoiceFixtureRunner()"

    async def run(
        self,
        command: RunAlphaVoiceFixtureCommand,
    ) -> AlphaVoiceFixtureResult:
        request = VoiceTranscriptionRequest(
            job_key=command.queued_job.key,
            metadata=command.metadata,
            audio_bytes=command.audio_bytes,
        )
        operation_ref = _fixture_operation_ref(command.audio_bytes)
        running = start_transcription_job(
            command.queued_job,
            now=command.started_at,
            provider_operation_ref=operation_ref,
        )
        transcription = await self._transcription.transcribe(request)
        completed = settle_transcription_job(
            running,
            transcription,
            now=command.completed_at,
        )
        if completed.transcript is None:
            raise AlphaVoiceDomainError("voice_fixture_transcript_missing")
        response = build_alpha_voice_response(
            transcript=completed.transcript,
            understanding=self._understanding,
            answer=self._answer,
        )
        return AlphaVoiceFixtureResult(
            completed_job=completed,
            response=response,
            operation_ref=operation_ref,
        )


def _fixture_operation_ref(audio_bytes: bytes) -> str:
    return f"fixture-{sha256(audio_bytes).hexdigest()[:24]}"
