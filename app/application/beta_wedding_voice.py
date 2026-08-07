"""Durable two-phase processing for a voice fact in the Wedding Beta Space.

The webhook may acknowledge the receipt synchronously, but every provider effect
is protected by a phase-specific reservation.  A voice can therefore produce
one receipt and one (separate) structured result without a retry ever guessing
whether Telegram or transcription already completed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

JsonObject = dict[str, object]
_MINSK_TZ = timezone(timedelta(hours=3))
_MAX_TELEGRAM_TEXT_CHARS = 4_096


class BetaWeddingVoicePhase(StrEnum):
    RECEIPT = "receipt"
    STRUCTURED_RESULT = "structured_result"


class BetaWeddingVoiceDisposition(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    DUPLICATE = "duplicate"
    SENT = "sent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BetaWeddingVoiceJob:
    """Private job data released only after an exact Space lookup."""

    job_id: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    telegram_file_id: str = field(repr=False)
    sender_label: str
    occurred_at: datetime
    duration_seconds: int | None


@dataclass(frozen=True, slots=True)
class BetaWeddingPhaseReservation:
    created: bool
    delivery_id: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    candidate_text: str | None = field(default=None, repr=False)


class BetaWeddingVoiceStore(Protocol):
    async def reserve_voice_receipt(
        self, *, receipt_id: str, now: datetime
    ) -> tuple[BetaWeddingVoiceJob, BetaWeddingPhaseReservation] | None: ...

    async def claim_phase(
        self, *, job_id: str, phase: BetaWeddingVoicePhase, now: datetime
    ) -> BetaWeddingPhaseReservation | None: ...

    async def settle_phase(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None: ...

    async def claim_transcription(self, *, job_id: str, now: datetime) -> BetaWeddingVoiceJob | None: ...

    async def persist_structured_result(
        self, *, job_id: str, transcript: str, structured_text: str, now: datetime
    ) -> bool: ...

    async def settle_transcription_failure(
        self, *, job_id: str, error_code: str, now: datetime
    ) -> None: ...


class BetaWeddingVoiceTelegram(Protocol):
    async def send_plain_message(self, *, chat_id: str, text: str) -> JsonObject: ...

    async def download_voice(self, *, file_id: str) -> bytes: ...


class BetaWeddingVoiceTranscriber(Protocol):
    async def transcribe(self, *, audio: bytes) -> str: ...


class BetaWeddingVoiceStructurer(Protocol):
    async def structure(self, *, transcript: str) -> str: ...


@dataclass(frozen=True, slots=True)
class BetaWeddingVoiceResult:
    disposition: BetaWeddingVoiceDisposition
    code: str


class ProcessBetaWeddingVoice:
    """Send receipt, transcribe exactly once, persist structure, then send it."""

    def __init__(
        self,
        *,
        store: BetaWeddingVoiceStore,
        telegram: BetaWeddingVoiceTelegram,
        transcriber: BetaWeddingVoiceTranscriber,
        structurer: BetaWeddingVoiceStructurer,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._transcriber = transcriber
        self._structurer = structurer

    async def execute(self, *, receipt_id: str, now: datetime) -> BetaWeddingVoiceResult:
        reserved = await self._store.reserve_voice_receipt(receipt_id=receipt_id, now=now)
        if reserved is None:
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.NOT_APPLICABLE, "voice_not_applicable")
        job, receipt = reserved
        if not receipt.created:
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.DUPLICATE, "voice_duplicate")
        if not await self._send(reservation=receipt, now=now):
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.UNKNOWN, "voice_receipt_unknown")

        claimed = await self._store.claim_transcription(job_id=job.job_id, now=now)
        if claimed is None:
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.DUPLICATE, "voice_transcription_duplicate")
        try:
            transcript = _bounded(await self._transcriber.transcribe(audio=await self._telegram.download_voice(file_id=claimed.telegram_file_id)))
            structured = _bounded(await self._structurer.structure(transcript=transcript))
        except Exception:
            await self._store.settle_transcription_failure(job_id=claimed.job_id, error_code="voice_transcription_unknown", now=now)
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.UNKNOWN, "voice_transcription_unknown")
        if not await self._store.persist_structured_result(job_id=claimed.job_id, transcript=transcript, structured_text=structured, now=now):
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.DUPLICATE, "voice_structure_duplicate")
        result = await self._store.claim_phase(job_id=claimed.job_id, phase=BetaWeddingVoicePhase.STRUCTURED_RESULT, now=now)
        if result is None:
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.DUPLICATE, "voice_result_duplicate")
        if not await self._send(reservation=result, now=now):
            return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.UNKNOWN, "voice_result_unknown")
        return BetaWeddingVoiceResult(BetaWeddingVoiceDisposition.SENT, "voice_structured_result_sent")

    async def _send(self, *, reservation: BetaWeddingPhaseReservation, now: datetime) -> bool:
        if reservation.delivery_id is None or reservation.telegram_chat_id is None or reservation.candidate_text is None:
            raise RuntimeError("beta_wedding_voice_delivery_invalid")
        try:
            response = await self._telegram.send_plain_message(chat_id=reservation.telegram_chat_id, text=reservation.candidate_text)
            result = response.get("result")
            provider_message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(provider_message_id) is not int:
                raise RuntimeError("beta_wedding_voice_delivery_unconfirmed")
        except Exception:
            await self._store.settle_phase(delivery_id=reservation.delivery_id, outcome="unknown", telegram_message_id=None, error_code="beta_wedding_voice_delivery_unknown", now=now)
            return False
        await self._store.settle_phase(delivery_id=reservation.delivery_id, outcome="sent", telegram_message_id=str(provider_message_id), error_code=None, now=now)
        return True


def voice_receipt_text(job: BetaWeddingVoiceJob) -> str:
    when = job.occurred_at.astimezone(_MINSK_TZ).strftime("%d.%m.%Y %H:%M МСК")
    duration = f"{job.duration_seconds} с" if job.duration_seconds is not None else "не подтверждена"
    return "\n".join(("Приняла голосовое сообщение:", f"- От: {job.sender_label}", f"- Время: {when}", f"- Длительность: {duration}", "Расшифровываю и затем сохраню структурированный результат в памяти свадьбы."))


def _bounded(value: str) -> str:
    result = value.strip()
    if not result:
        raise RuntimeError("beta_wedding_voice_empty")
    return result if len(result) <= _MAX_TELEGRAM_TEXT_CHARS else result[: _MAX_TELEGRAM_TEXT_CHARS - 1].rstrip() + "…"
