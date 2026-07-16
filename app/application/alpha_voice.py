"""Fail-closed application contracts for Telegram voice addressed to Alpha.

This module deliberately has no ORM, Telegram download, OpenAI, logging, or send
implementation.  A worker may call a transcription provider only after it has
durably moved a unique job from ``queued`` to ``running``.  A running job whose
provider outcome cannot be proved becomes ``unknown`` and cannot be retried
automatically.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol

from app.application.alpha_access import (
    AlphaAccessFacts,
    AlphaAccessReason,
    AlphaAccessRoute,
    AlphaContentKind,
    evaluate_alpha_access,
)
from app.application.alpha_canary import ALPHA_DISCLOSURE_PREFIX
from app.application.alpha_delivery import MAX_TELEGRAM_TEXT_CHARS

MAX_TELEGRAM_VOICE_BYTES = 19_000_000
MAX_TELEGRAM_VOICE_DURATION_SECONDS = 600
MAX_ALPHA_VOICE_RESPONSE_CHARS = (
    MAX_TELEGRAM_TEXT_CHARS - len(ALPHA_DISCLOSURE_PREFIX)
)
TRANSCRIPTION_OPERATION: Literal["transcription"] = "transcription"
EMPTY_TRANSCRIPT_CLARIFICATION = (
    "Не удалось надёжно разобрать голосовое сообщение. "
    "Пожалуйста, повторите его или напишите текстом."
)


class AlphaVoiceDomainError(ValueError):
    """A stable machine-readable invariant violation without sensitive values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AlphaVoiceIntakeDisposition(StrEnum):
    QUEUED = "queued"
    TERMINAL = "terminal"


class AlphaVoiceIntakeCode(StrEnum):
    QUEUED = "alpha_voice_queued"
    ACCESS_DENIED = "alpha_voice_access_denied"
    MEDIA_UNSUPPORTED = "alpha_voice_media_unsupported"
    METADATA_MISSING = "alpha_voice_metadata_missing"
    METADATA_MALFORMED = "alpha_voice_metadata_malformed"
    MIME_UNSUPPORTED = "alpha_voice_mime_unsupported"
    FILE_TOO_LARGE = "alpha_voice_file_too_large"
    DURATION_TOO_LONG = "alpha_voice_duration_too_long"


@dataclass(frozen=True, slots=True)
class TelegramVoiceMetadata:
    """Validated Telegram ``Voice`` metadata; provider identifiers stay private."""

    file_id: str = field(repr=False)
    file_unique_id: str = field(repr=False)
    duration_seconds: int
    file_size_bytes: int | None
    mime_type: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_token(self.file_id, "voice_file_id_invalid", 512)
        _require_token(self.file_unique_id, "voice_file_unique_id_invalid", 128)
        if isinstance(self.duration_seconds, bool) or self.duration_seconds < 1:
            raise AlphaVoiceDomainError("voice_duration_invalid")
        if self.file_size_bytes is not None and (
            isinstance(self.file_size_bytes, bool)
            or not isinstance(self.file_size_bytes, int)
            or self.file_size_bytes < 1
        ):
            raise AlphaVoiceDomainError("voice_file_size_invalid")
        if self.mime_type is not None:
            _require_text(self.mime_type, "voice_mime_type_invalid", 128)


@dataclass(frozen=True, slots=True)
class TranscriptionJobKey:
    """The durable idempotency identity required for one transcription operation."""

    workspace_key: str
    canonical_message_id: str = field(repr=False)
    operation: Literal["transcription"] = TRANSCRIPTION_OPERATION

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "voice_workspace_key_invalid", 128)
        _require_token(
            self.canonical_message_id,
            "voice_canonical_message_id_invalid",
            128,
        )
        if self.operation != TRANSCRIPTION_OPERATION:
            raise AlphaVoiceDomainError("voice_operation_invalid")

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.workspace_key, self.canonical_message_id, self.operation)


@dataclass(frozen=True, slots=True)
class EvaluateAlphaVoiceCommand:
    """Policy facts plus the untrusted Telegram voice object for one message."""

    workspace_key: str
    canonical_message_id: str = field(repr=False)
    access: AlphaAccessFacts = field(repr=False)
    media_kind: str | None
    voice: Mapping[str, object] | None = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "voice_workspace_key_invalid", 128)
        _require_token(
            self.canonical_message_id,
            "voice_canonical_message_id_invalid",
            128,
        )
        if not isinstance(self.access, AlphaAccessFacts):
            raise AlphaVoiceDomainError("voice_access_facts_invalid")


@dataclass(frozen=True, slots=True)
class AlphaVoiceIntakeDecision:
    disposition: AlphaVoiceIntakeDisposition
    code: AlphaVoiceIntakeCode
    access_reason: AlphaAccessReason
    terminal: bool
    retryable: bool
    job_key: TranscriptionJobKey | None = field(default=None, repr=False)
    metadata: TelegramVoiceMetadata | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.access_reason, AlphaAccessReason):
            raise AlphaVoiceDomainError("voice_access_reason_invalid")
        if self.code is AlphaVoiceIntakeCode.ACCESS_DENIED:
            if self.access_reason is AlphaAccessReason.ALLOWED:
                raise AlphaVoiceDomainError("voice_access_decision_inconsistent")
        elif self.access_reason is not AlphaAccessReason.ALLOWED:
            raise AlphaVoiceDomainError("voice_access_decision_inconsistent")
        queued = self.disposition is AlphaVoiceIntakeDisposition.QUEUED
        if queued != (self.code is AlphaVoiceIntakeCode.QUEUED):
            raise AlphaVoiceDomainError("voice_intake_decision_inconsistent")
        if queued and (self.terminal or self.retryable):
            raise AlphaVoiceDomainError("voice_queued_decision_inconsistent")
        if queued and (self.job_key is None or self.metadata is None):
            raise AlphaVoiceDomainError("voice_queued_payload_missing")
        if not queued and (not self.terminal or self.retryable):
            raise AlphaVoiceDomainError("voice_terminal_decision_inconsistent")
        if not queued and (self.job_key is not None or self.metadata is not None):
            raise AlphaVoiceDomainError("voice_terminal_payload_forbidden")


def evaluate_alpha_voice(command: EvaluateAlphaVoiceCommand) -> AlphaVoiceIntakeDecision:
    """Return one deterministic, fail-closed decision without provider or I/O calls."""

    access = evaluate_alpha_access(command.access)
    if access.route is not AlphaAccessRoute.PROVIDER:
        return _terminal(
            AlphaVoiceIntakeCode.ACCESS_DENIED,
            access_reason=access.reason,
        )
    if command.access.content_kind is not AlphaContentKind.VOICE:
        return _terminal(
            AlphaVoiceIntakeCode.MEDIA_UNSUPPORTED,
            access_reason=access.reason,
        )
    if command.media_kind is None:
        return _terminal(
            AlphaVoiceIntakeCode.METADATA_MISSING,
            access_reason=access.reason,
        )
    if not isinstance(command.media_kind, str):
        return _terminal(
            AlphaVoiceIntakeCode.MEDIA_UNSUPPORTED,
            access_reason=access.reason,
        )
    if command.media_kind != "voice":
        return _terminal(
            AlphaVoiceIntakeCode.MEDIA_UNSUPPORTED,
            access_reason=access.reason,
        )
    if command.voice is None:
        return _terminal(
            AlphaVoiceIntakeCode.METADATA_MISSING,
            access_reason=access.reason,
        )
    if not isinstance(command.voice, Mapping):
        return _terminal(
            AlphaVoiceIntakeCode.METADATA_MALFORMED,
            access_reason=access.reason,
        )

    parsed = _parse_telegram_voice(command.voice)
    if isinstance(parsed, AlphaVoiceIntakeCode):
        return _terminal(parsed, access_reason=access.reason)

    if (
        parsed.file_size_bytes is not None
        and parsed.file_size_bytes > MAX_TELEGRAM_VOICE_BYTES
    ):
        return _terminal(
            AlphaVoiceIntakeCode.FILE_TOO_LARGE,
            access_reason=access.reason,
        )
    if parsed.duration_seconds > MAX_TELEGRAM_VOICE_DURATION_SECONDS:
        return _terminal(
            AlphaVoiceIntakeCode.DURATION_TOO_LONG,
            access_reason=access.reason,
        )
    if not _supported_voice_mime(parsed.mime_type):
        return _terminal(
            AlphaVoiceIntakeCode.MIME_UNSUPPORTED,
            access_reason=access.reason,
        )

    return AlphaVoiceIntakeDecision(
        disposition=AlphaVoiceIntakeDisposition.QUEUED,
        code=AlphaVoiceIntakeCode.QUEUED,
        access_reason=access.reason,
        terminal=False,
        retryable=False,
        job_key=TranscriptionJobKey(
            workspace_key=command.workspace_key,
            canonical_message_id=command.canonical_message_id,
        ),
        metadata=parsed,
    )


def _parse_telegram_voice(
    voice: Mapping[str, object],
) -> TelegramVoiceMetadata | AlphaVoiceIntakeCode:
    required = {"file_id", "file_unique_id", "duration"}
    if not required.issubset(voice):
        return AlphaVoiceIntakeCode.METADATA_MISSING
    file_id = voice.get("file_id")
    file_unique_id = voice.get("file_unique_id")
    duration = voice.get("duration")
    file_size = voice.get("file_size")
    mime_type = voice.get("mime_type")
    if (
        not isinstance(file_id, str)
        or not isinstance(file_unique_id, str)
        or not isinstance(duration, int)
        or isinstance(duration, bool)
        or (
            file_size is not None
            and (not isinstance(file_size, int) or isinstance(file_size, bool))
        )
        or (mime_type is not None and not isinstance(mime_type, str))
    ):
        return AlphaVoiceIntakeCode.METADATA_MALFORMED
    try:
        return TelegramVoiceMetadata(
            file_id=file_id,
            file_unique_id=file_unique_id,
            duration_seconds=duration,
            file_size_bytes=file_size,
            mime_type=mime_type,
        )
    except AlphaVoiceDomainError:
        return AlphaVoiceIntakeCode.METADATA_MALFORMED


def _supported_voice_mime(mime_type: str | None) -> bool:
    if mime_type is None:
        return True
    base_type = mime_type.split(";", 1)[0].strip().casefold()
    return base_type in {"audio/ogg", "audio/opus"}


def _terminal(
    code: AlphaVoiceIntakeCode,
    *,
    access_reason: AlphaAccessReason,
) -> AlphaVoiceIntakeDecision:
    return AlphaVoiceIntakeDecision(
        disposition=AlphaVoiceIntakeDisposition.TERMINAL,
        code=code,
        access_reason=access_reason,
        terminal=True,
        retryable=False,
    )


class TranscriptionJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionJob:
    key: TranscriptionJobKey = field(repr=False)
    state: TranscriptionJobState
    attempts: int
    updated_at: datetime
    transcript: str | None = field(default=None, repr=False)
    error_code: str | None = None
    provider_operation_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_aware(self.updated_at, "voice_job_updated_at_timezone_required")
        if self.attempts not in {0, 1}:
            raise AlphaVoiceDomainError("voice_job_attempts_invalid")
        if self.error_code is not None:
            _require_token(self.error_code, "voice_job_error_code_invalid", 128)
        if self.provider_operation_ref is not None:
            _require_token(
                self.provider_operation_ref,
                "voice_provider_operation_ref_invalid",
                255,
            )
        if self.transcript is not None and len(self.transcript) > 100_000:
            raise AlphaVoiceDomainError("voice_transcript_too_large")
        if self.state is TranscriptionJobState.QUEUED:
            if self.attempts != 0 or any(
                value is not None
                for value in (self.transcript, self.error_code, self.provider_operation_ref)
            ):
                raise AlphaVoiceDomainError("voice_queued_job_inconsistent")
        elif self.attempts != 1:
            raise AlphaVoiceDomainError("voice_started_job_attempt_missing")
        if self.state is TranscriptionJobState.RUNNING and (
            self.transcript is not None or self.error_code is not None
        ):
            raise AlphaVoiceDomainError("voice_running_job_inconsistent")
        if self.state is TranscriptionJobState.COMPLETED and (
            self.transcript is None or self.error_code is not None
        ):
            raise AlphaVoiceDomainError("voice_completed_job_inconsistent")
        if self.state in {TranscriptionJobState.FAILED, TranscriptionJobState.UNKNOWN} and (
            self.transcript is not None or self.error_code is None
        ):
            raise AlphaVoiceDomainError("voice_terminal_job_inconsistent")

    @property
    def automatic_provider_call_allowed(self) -> bool:
        """Only a never-started queued job may cause a provider call."""

        return self.state is TranscriptionJobState.QUEUED and self.attempts == 0


def new_queued_transcription_job(
    key: TranscriptionJobKey, *, now: datetime
) -> VoiceTranscriptionJob:
    return VoiceTranscriptionJob(
        key=key,
        state=TranscriptionJobState.QUEUED,
        attempts=0,
        updated_at=now,
    )


def start_transcription_job(
    job: VoiceTranscriptionJob,
    *,
    now: datetime,
    provider_operation_ref: str,
) -> VoiceTranscriptionJob:
    if not job.automatic_provider_call_allowed:
        raise AlphaVoiceDomainError("voice_job_not_startable")
    _require_not_before(now, job.updated_at)
    return VoiceTranscriptionJob(
        key=job.key,
        state=TranscriptionJobState.RUNNING,
        attempts=1,
        updated_at=now,
        provider_operation_ref=provider_operation_ref,
    )


class TranscriptionOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionResult:
    outcome: TranscriptionOutcome
    transcript: str | None = field(default=None, repr=False)
    error_code: str | None = None
    provider_operation_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.transcript is not None and len(self.transcript) > 100_000:
            raise AlphaVoiceDomainError("voice_transcript_too_large")
        if self.error_code is not None:
            _require_token(self.error_code, "voice_transcription_error_code_invalid", 128)
        if self.provider_operation_ref is not None:
            _require_token(
                self.provider_operation_ref,
                "voice_provider_operation_ref_invalid",
                255,
            )
        if self.outcome is TranscriptionOutcome.COMPLETED:
            if self.transcript is None or self.error_code is not None:
                raise AlphaVoiceDomainError("voice_transcription_result_inconsistent")
        elif self.transcript is not None or self.error_code is None:
            raise AlphaVoiceDomainError("voice_transcription_result_inconsistent")


def settle_transcription_job(
    job: VoiceTranscriptionJob,
    result: VoiceTranscriptionResult,
    *,
    now: datetime,
) -> VoiceTranscriptionJob:
    if job.state is not TranscriptionJobState.RUNNING:
        raise AlphaVoiceDomainError("voice_job_not_running")
    _require_not_before(now, job.updated_at)
    if (
        result.provider_operation_ref is not None
        and job.provider_operation_ref is not None
        and result.provider_operation_ref != job.provider_operation_ref
    ):
        raise AlphaVoiceDomainError("voice_provider_operation_ref_mismatch")
    provider_ref = result.provider_operation_ref or job.provider_operation_ref
    if result.outcome is TranscriptionOutcome.COMPLETED:
        return VoiceTranscriptionJob(
            key=job.key,
            state=TranscriptionJobState.COMPLETED,
            attempts=job.attempts,
            updated_at=now,
            transcript=result.transcript,
            provider_operation_ref=provider_ref,
        )
    return VoiceTranscriptionJob(
        key=job.key,
        state=(
            TranscriptionJobState.FAILED
            if result.outcome is TranscriptionOutcome.FAILED
            else TranscriptionJobState.UNKNOWN
        ),
        attempts=job.attempts,
        updated_at=now,
        error_code=result.error_code,
        provider_operation_ref=provider_ref,
    )


def mark_ambiguous_provider_outcome(
    job: VoiceTranscriptionJob, *, now: datetime
) -> VoiceTranscriptionJob:
    """Quarantine a stale running attempt; the same key cannot be auto-retried."""

    return settle_transcription_job(
        job,
        VoiceTranscriptionResult(
            outcome=TranscriptionOutcome.UNKNOWN,
            error_code="alpha_voice_provider_outcome_ambiguous",
            provider_operation_ref=job.provider_operation_ref,
        ),
        now=now,
    )


class VoiceTranscriptionJobStore(Protocol):
    """Persistence port; implementations enforce a unique constraint on ``key``."""

    async def enqueue_once(
        self, key: TranscriptionJobKey, *, now: datetime
    ) -> tuple[VoiceTranscriptionJob, bool]:
        """Return ``(job, created)`` for the exact idempotency key."""

    async def compare_and_set(
        self,
        *,
        expected: VoiceTranscriptionJob,
        updated: VoiceTranscriptionJob,
    ) -> VoiceTranscriptionJob:
        """Persist one validated state transition with optimistic concurrency."""


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionRequest:
    job_key: TranscriptionJobKey = field(repr=False)
    metadata: TelegramVoiceMetadata = field(repr=False)
    audio_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        size = len(self.audio_bytes)
        if size < 1:
            raise AlphaVoiceDomainError("voice_audio_empty")
        if size > MAX_TELEGRAM_VOICE_BYTES:
            raise AlphaVoiceDomainError("voice_audio_too_large")
        if (
            self.metadata.file_size_bytes is not None
            and size != self.metadata.file_size_bytes
        ):
            raise AlphaVoiceDomainError("voice_audio_size_mismatch")


class VoiceTranscriptionPort(Protocol):
    async def transcribe(self, request: VoiceTranscriptionRequest) -> VoiceTranscriptionResult: ...


class DeterministicFixtureTranscriptionPort:
    """In-process fixture adapter; it performs no network or filesystem access."""

    def __init__(self, transcript: str) -> None:
        if len(transcript) > 100_000:
            raise AlphaVoiceDomainError("voice_transcript_too_large")
        self._transcript = transcript

    def __repr__(self) -> str:
        return "DeterministicFixtureTranscriptionPort()"

    async def transcribe(self, request: VoiceTranscriptionRequest) -> VoiceTranscriptionResult:
        fingerprint = sha256(request.audio_bytes).hexdigest()[:24]
        return VoiceTranscriptionResult(
            outcome=TranscriptionOutcome.COMPLETED,
            transcript=self._transcript,
            provider_operation_ref=f"fixture-{fingerprint}",
        )


class AlphaVoiceResponseKind(StrEnum):
    ANSWER = "answer"
    CLARIFICATION = "clarification"


@dataclass(frozen=True, slots=True)
class AlphaVoiceResponse:
    kind: AlphaVoiceResponseKind
    understanding: str | None = field(default=None, repr=False)
    answer: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.kind is AlphaVoiceResponseKind.ANSWER:
            if self.understanding is None or self.answer is None:
                raise AlphaVoiceDomainError("voice_answer_fields_missing")
            _require_text(self.understanding, "voice_understanding_invalid", 8_192)
            _require_text(self.answer, "voice_answer_invalid", 16_384)
            if len(self.render()) > MAX_ALPHA_VOICE_RESPONSE_CHARS:
                raise AlphaVoiceDomainError("voice_response_too_large")
        elif self.understanding is not None or self.answer is not None:
            raise AlphaVoiceDomainError("voice_clarification_fields_forbidden")

    def render(self) -> str:
        if self.kind is AlphaVoiceResponseKind.CLARIFICATION:
            return EMPTY_TRANSCRIPT_CLARIFICATION
        return f"Я понял так: {self.understanding}\n\nОтвет: {self.answer}"


def build_alpha_voice_response(
    *, transcript: str, understanding: str, answer: str
) -> AlphaVoiceResponse:
    """Build the disclosure shape, or ask for clarification after empty speech."""

    if len(transcript) > 100_000:
        raise AlphaVoiceDomainError("voice_transcript_too_large")
    if not transcript.strip():
        return AlphaVoiceResponse(kind=AlphaVoiceResponseKind.CLARIFICATION)
    return AlphaVoiceResponse(
        kind=AlphaVoiceResponseKind.ANSWER,
        understanding=understanding,
        answer=answer,
    )


def _require_token(value: str, code: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise AlphaVoiceDomainError(code)


def _require_text(value: str, code: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AlphaVoiceDomainError(code)


def _require_aware(value: datetime, code: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AlphaVoiceDomainError(code)


def _require_not_before(value: datetime, earlier: datetime) -> None:
    _require_aware(value, "voice_job_updated_at_timezone_required")
    if value < earlier:
        raise AlphaVoiceDomainError("voice_job_time_regression")
