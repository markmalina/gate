"""SQL implementation of the exact Wedding voice phase ledger."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.beta_wedding_voice import (
    BetaWeddingPhaseReservation,
    BetaWeddingVoiceJob,
    BetaWeddingVoicePhase,
    voice_receipt_text,
)
from app.infrastructure.beta_wedding_binding import BETA_WEDDING_TENANT_REF
from app.models.beta_space import BetaSpaceBindingModel, BetaSpaceEventModel
from app.models.beta_wedding_voice import BetaWeddingVoiceDeliveryModel, BetaWeddingVoiceJobModel
from app.models.domain import TelegramReceipt


class SqlBetaWeddingVoiceStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve_voice_receipt(self, *, receipt_id: str, now: datetime):
        try:
            parsed = uuid.UUID(receipt_id)
        except ValueError:
            return None
        row = (await self._session.execute(select(BetaSpaceEventModel, BetaSpaceBindingModel, TelegramReceipt).join(BetaSpaceBindingModel, BetaSpaceBindingModel.id == BetaSpaceEventModel.binding_id).join(TelegramReceipt, TelegramReceipt.id == BetaSpaceEventModel.receipt_id).where(BetaSpaceEventModel.receipt_id == parsed, BetaSpaceEventModel.media_kind == "voice", BetaSpaceEventModel.event_kind == "fact", BetaSpaceBindingModel.tenant_ref == BETA_WEDDING_TENANT_REF, BetaSpaceBindingModel.state == "consented").with_for_update(of=BetaSpaceEventModel))).one_or_none()
        if row is None:
            await self._session.rollback()
            return None
        event, binding, receipt = row
        existing = await self._session.scalar(select(BetaWeddingVoiceJobModel).where(BetaWeddingVoiceJobModel.event_id == event.id).with_for_update())
        if existing is not None:
            await self._session.rollback()
            return _job(existing, binding.telegram_chat_id), BetaWeddingPhaseReservation(False)
        projection = _voice_projection(receipt.payload, expected_chat_id=binding.telegram_chat_id)
        if projection is None:
            await self._session.rollback()
            return None
        file_id, sender, occurred_at, duration = projection
        job = BetaWeddingVoiceJobModel(id=uuid.uuid4(), event_id=event.id, workspace_id=binding.workspace_id, binding_id=binding.id, telegram_file_id=file_id, sender_label=sender, occurred_at=occurred_at, duration_seconds=duration, state="receipt_pending")
        self._session.add(job)
        public = _job(job, binding.telegram_chat_id)
        self._session.add(BetaWeddingVoiceDeliveryModel(id=uuid.uuid4(), job_id=job.id, phase=BetaWeddingVoicePhase.RECEIPT.value, candidate_text=voice_receipt_text(public), candidate_sha256=_sha(voice_receipt_text(public)), state="sending", reserved_at=now))
        await self._session.commit()
        return public, BetaWeddingPhaseReservation(True, "receipt:" + str(job.id), binding.telegram_chat_id, voice_receipt_text(public))

    async def claim_phase(self, *, job_id: str, phase: BetaWeddingVoicePhase, now: datetime):
        job = await self._job_locked(job_id)
        if job is None or job.state != "structured" or phase is not BetaWeddingVoicePhase.STRUCTURED_RESULT:
            await self._session.rollback()
            return None
        delivery = await self._session.scalar(select(BetaWeddingVoiceDeliveryModel).where(BetaWeddingVoiceDeliveryModel.job_id == job.id, BetaWeddingVoiceDeliveryModel.phase == phase.value).with_for_update())
        if delivery is None or delivery.state != "pending":
            await self._session.rollback()
            return None
        delivery.state, delivery.reserved_at, delivery.updated_at = "sending", now, now
        chat_id = await self._session.scalar(select(BetaSpaceBindingModel.telegram_chat_id).where(BetaSpaceBindingModel.id == job.binding_id, BetaSpaceBindingModel.state == "consented"))
        if not isinstance(chat_id, str):
            await self._session.rollback()
            return None
        await self._session.commit()
        return BetaWeddingPhaseReservation(True, str(delivery.id), chat_id, delivery.candidate_text)

    async def settle_phase(self, *, delivery_id: str, outcome: str, telegram_message_id: str | None, error_code: str | None, now: datetime) -> None:
        delivery = await self._delivery_locked(delivery_id)
        if delivery is None or delivery.state != "sending" or outcome not in {"sent", "unknown"}:
            await self._session.rollback()
            raise RuntimeError("beta_wedding_voice_delivery_state_conflict")
        delivery.state, delivery.provider_message_id, delivery.error_code, delivery.settled_at, delivery.updated_at = outcome, telegram_message_id if outcome == "sent" else None, error_code, now, now
        job = await self._session.scalar(select(BetaWeddingVoiceJobModel).where(BetaWeddingVoiceJobModel.id == delivery.job_id).with_for_update())
        if job is not None and delivery.phase == BetaWeddingVoicePhase.RECEIPT.value:
            job.state, job.updated_at = ("receipt_sent" if outcome == "sent" else "failed"), now
        await self._session.commit()

    async def claim_transcription(self, *, job_id: str, now: datetime):
        job = await self._job_locked(job_id)
        if job is None or job.state != "receipt_sent":
            await self._session.rollback()
            return None
        chat_id = await self._session.scalar(select(BetaSpaceBindingModel.telegram_chat_id).where(BetaSpaceBindingModel.id == job.binding_id, BetaSpaceBindingModel.state == "consented"))
        if not isinstance(chat_id, str):
            await self._session.rollback()
            return None
        job.state, job.updated_at = "transcribing", now
        await self._session.commit()
        return _job(job, chat_id)

    async def persist_structured_result(self, *, job_id: str, transcript: str, structured_text: str, now: datetime) -> bool:
        job = await self._job_locked(job_id)
        if job is None or job.state != "transcribing":
            await self._session.rollback()
            return False
        job.transcript_text, job.structured_text, job.transcript_sha256, job.structured_sha256 = transcript, structured_text, _sha(transcript), _sha(structured_text)
        job.state, job.transcribed_at, job.updated_at = "structured", now, now
        self._session.add(BetaWeddingVoiceDeliveryModel(id=uuid.uuid4(), job_id=job.id, phase=BetaWeddingVoicePhase.STRUCTURED_RESULT.value, candidate_text=structured_text, candidate_sha256=_sha(structured_text), state="pending"))
        await self._session.commit()
        return True

    async def settle_transcription_failure(self, *, job_id: str, error_code: str, now: datetime) -> None:
        job = await self._job_locked(job_id)
        if job is None or job.state != "transcribing":
            await self._session.rollback()
            return
        job.state, job.error_code, job.updated_at = "failed", error_code, now
        await self._session.commit()

    async def _job_locked(self, job_id: str) -> BetaWeddingVoiceJobModel | None:
        try:
            return cast(BetaWeddingVoiceJobModel | None, await self._session.scalar(select(BetaWeddingVoiceJobModel).where(BetaWeddingVoiceJobModel.id == uuid.UUID(job_id)).with_for_update()))
        except ValueError:
            return None

    async def _delivery_locked(self, delivery_id: str) -> BetaWeddingVoiceDeliveryModel | None:
        try:
            if delivery_id.startswith("receipt:"):
                return cast(BetaWeddingVoiceDeliveryModel | None, await self._session.scalar(select(BetaWeddingVoiceDeliveryModel).where(BetaWeddingVoiceDeliveryModel.job_id == uuid.UUID(delivery_id.removeprefix("receipt:")), BetaWeddingVoiceDeliveryModel.phase == "receipt").with_for_update()))
            return cast(BetaWeddingVoiceDeliveryModel | None, await self._session.scalar(select(BetaWeddingVoiceDeliveryModel).where(BetaWeddingVoiceDeliveryModel.id == uuid.UUID(delivery_id)).with_for_update()))
        except ValueError:
            return None


def _job(row: BetaWeddingVoiceJobModel, chat_id: str) -> BetaWeddingVoiceJob:
    return BetaWeddingVoiceJob(str(row.id), chat_id, row.telegram_file_id, row.sender_label, row.occurred_at, row.duration_seconds)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _voice_projection(payload: object, *, expected_chat_id: str):
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        return None
    message = payload["message"]
    chat, actor, voice = message.get("chat"), message.get("from"), message.get("voice")
    if not isinstance(chat, dict) or str(chat.get("id")) != expected_chat_id or not isinstance(actor, dict) or actor.get("is_bot") is not False or not isinstance(voice, dict):
        return None
    file_id, stamp, duration = voice.get("file_id"), message.get("date"), voice.get("duration")
    if not isinstance(file_id, str) or not file_id or type(stamp) is not int or stamp < 0 or (duration is not None and (type(duration) is not int or duration < 0)):
        return None
    labels = [part.strip() for part in (actor.get("first_name"), actor.get("last_name")) if isinstance(part, str) and part.strip()]
    return file_id, (" ".join(labels) or "Участник")[:128], datetime.fromtimestamp(stamp, UTC), duration
