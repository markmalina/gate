"""SQL persistence for provider-neutral Beta material processing."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.beta_material import (
    BetaMaterialJob,
    BetaMaterialPhase,
    BetaMaterialPhaseReservation,
    material_receipt_text,
    safe_failure_text,
)
from app.models.beta_material import (
    BetaMaterialDeliveryModel,
    BetaMaterialJobModel,
)
from app.models.beta_space import BetaSpaceBindingModel, BetaSpaceEventModel
from app.models.domain import TelegramReceipt

JsonObject = dict[str, Any]


class SqlBetaMaterialStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve_material_receipt(
        self, *, receipt_id: str, now: datetime
    ) -> tuple[BetaMaterialJob, BetaMaterialPhaseReservation] | None:
        parsed = _uuid(receipt_id)
        if parsed is None:
            return None
        row = (
            await self._session.execute(
                select(BetaSpaceEventModel, BetaSpaceBindingModel, TelegramReceipt)
                .join(
                    BetaSpaceBindingModel,
                    BetaSpaceBindingModel.id == BetaSpaceEventModel.binding_id,
                )
                .join(
                    TelegramReceipt,
                    TelegramReceipt.id == BetaSpaceEventModel.receipt_id,
                )
                .where(
                    BetaSpaceEventModel.receipt_id == parsed,
                    BetaSpaceEventModel.media_kind.in_(
                        ("voice", "document", "photo")
                    ),
                    BetaSpaceEventModel.event_kind == "fact",
                    BetaSpaceBindingModel.state == "consented",
                )
                .with_for_update(of=BetaSpaceEventModel)
            )
        ).one_or_none()
        if row is None:
            await self._session.rollback()
            return None
        event, binding, receipt = row
        existing = await self._session.scalar(
            select(BetaMaterialJobModel)
            .where(BetaMaterialJobModel.event_id == event.id)
            .with_for_update()
        )
        if existing is not None:
            await self._session.rollback()
            return _job(existing, binding.telegram_chat_id), (
                BetaMaterialPhaseReservation(False)
            )
        projection = _material_projection(
            receipt.payload,
            expected_chat_id=binding.telegram_chat_id,
            expected_kind=event.media_kind,
        )
        if projection is None:
            await self._session.rollback()
            return None
        job = BetaMaterialJobModel(
            id=uuid.uuid4(),
            event_id=event.id,
            workspace_id=binding.workspace_id,
            binding_id=binding.id,
            media_kind=projection["media_kind"],
            telegram_file_id=projection["file_id"],
            sender_label=projection["sender_label"],
            occurred_at=projection["occurred_at"],
            file_name=projection["file_name"],
            mime_type=projection["mime_type"],
            file_size=projection["file_size"],
            duration_seconds=projection["duration_seconds"],
            state="receipt_pending",
        )
        self._session.add(job)
        await self._session.flush()
        public = _job(job, binding.telegram_chat_id)
        candidate = material_receipt_text(public)
        delivery = BetaMaterialDeliveryModel(
            id=uuid.uuid4(),
            job_id=job.id,
            phase=BetaMaterialPhase.RECEIPT.value,
            candidate_text=candidate,
            candidate_sha256=_sha(candidate),
            state="sending",
            reserved_at=now,
        )
        self._session.add(delivery)
        await self._session.commit()
        return public, BetaMaterialPhaseReservation(
            True,
            str(delivery.id),
            binding.telegram_chat_id,
            candidate,
        )

    async def claim_processing(
        self, *, job_id: str, now: datetime
    ) -> BetaMaterialJob | None:
        job = await self._job_locked(job_id)
        if job is None or job.state != "receipt_sent":
            await self._session.rollback()
            return None
        chat_id = await self._active_chat(job.binding_id)
        if chat_id is None:
            await self._session.rollback()
            return None
        job.state = "processing"
        job.updated_at = now
        await self._session.commit()
        return _job(job, chat_id)

    async def persist_result(
        self,
        *,
        job_id: str,
        source_file_sha256: str,
        extracted_text: str,
        structured_text: str,
        now: datetime,
    ) -> bool:
        job = await self._job_locked(job_id)
        if (
            job is None
            or job.state != "processing"
            or len(source_file_sha256) != 64
        ):
            await self._session.rollback()
            return False
        job.source_file_sha256 = source_file_sha256
        job.extracted_text = extracted_text
        job.structured_text = structured_text
        job.extracted_sha256 = _sha(extracted_text)
        job.structured_sha256 = _sha(structured_text)
        job.state = "structured"
        job.processed_at = now
        job.updated_at = now
        self._session.add(
            BetaMaterialDeliveryModel(
                id=uuid.uuid4(),
                job_id=job.id,
                phase=BetaMaterialPhase.STRUCTURED_RESULT.value,
                candidate_text=structured_text,
                candidate_sha256=_sha(structured_text),
                state="pending",
            )
        )
        await self._session.commit()
        return True

    async def settle_processing_failure(
        self, *, job_id: str, error_code: str, now: datetime
    ) -> BetaMaterialPhaseReservation | None:
        job = await self._job_locked(job_id)
        if job is None or job.state != "processing":
            await self._session.rollback()
            return None
        chat_id = await self._active_chat(job.binding_id)
        if chat_id is None:
            await self._session.rollback()
            return None
        candidate = safe_failure_text(job.media_kind, error_code)
        job.state = "failed"
        job.error_code = error_code
        job.processed_at = now
        job.updated_at = now
        delivery = BetaMaterialDeliveryModel(
            id=uuid.uuid4(),
            job_id=job.id,
            phase=BetaMaterialPhase.STRUCTURED_RESULT.value,
            candidate_text=candidate,
            candidate_sha256=_sha(candidate),
            state="sending",
            reserved_at=now,
        )
        self._session.add(delivery)
        await self._session.commit()
        return BetaMaterialPhaseReservation(
            True, str(delivery.id), chat_id, candidate
        )

    async def claim_phase(
        self, *, job_id: str, phase: BetaMaterialPhase, now: datetime
    ) -> BetaMaterialPhaseReservation | None:
        job = await self._job_locked(job_id)
        if (
            job is None
            or job.state != "structured"
            or phase is not BetaMaterialPhase.STRUCTURED_RESULT
        ):
            await self._session.rollback()
            return None
        delivery = await self._session.scalar(
            select(BetaMaterialDeliveryModel)
            .where(
                BetaMaterialDeliveryModel.job_id == job.id,
                BetaMaterialDeliveryModel.phase == phase.value,
            )
            .with_for_update()
        )
        if delivery is None or delivery.state != "pending":
            await self._session.rollback()
            return None
        chat_id = await self._active_chat(job.binding_id)
        if chat_id is None:
            await self._session.rollback()
            return None
        delivery.state = "sending"
        delivery.reserved_at = now
        delivery.updated_at = now
        await self._session.commit()
        return BetaMaterialPhaseReservation(
            True, str(delivery.id), chat_id, delivery.candidate_text
        )

    async def settle_phase(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None:
        delivery = await self._delivery_locked(delivery_id)
        if (
            delivery is None
            or delivery.state != "sending"
            or outcome not in {"sent", "unknown"}
        ):
            await self._session.rollback()
            raise RuntimeError("beta_material_delivery_state_conflict")
        delivery.state = outcome
        delivery.provider_message_id = (
            telegram_message_id if outcome == "sent" else None
        )
        delivery.error_code = error_code
        delivery.settled_at = now
        delivery.updated_at = now
        if delivery.phase == BetaMaterialPhase.RECEIPT.value:
            job = await self._session.scalar(
                select(BetaMaterialJobModel)
                .where(BetaMaterialJobModel.id == delivery.job_id)
                .with_for_update()
            )
            if job is not None:
                job.state = "receipt_sent" if outcome == "sent" else "failed"
                job.updated_at = now
        await self._session.commit()

    async def _job_locked(self, job_id: str) -> BetaMaterialJobModel | None:
        parsed = _uuid(job_id)
        if parsed is None:
            return None
        return cast(
            BetaMaterialJobModel | None,
            await self._session.scalar(
                select(BetaMaterialJobModel)
                .where(BetaMaterialJobModel.id == parsed)
                .with_for_update()
            ),
        )

    async def _delivery_locked(
        self, delivery_id: str
    ) -> BetaMaterialDeliveryModel | None:
        parsed = _uuid(delivery_id)
        if parsed is None:
            return None
        return cast(
            BetaMaterialDeliveryModel | None,
            await self._session.scalar(
                select(BetaMaterialDeliveryModel)
                .where(BetaMaterialDeliveryModel.id == parsed)
                .with_for_update()
            ),
        )

    async def _active_chat(self, binding_id: uuid.UUID) -> str | None:
        value = await self._session.scalar(
            select(BetaSpaceBindingModel.telegram_chat_id).where(
                BetaSpaceBindingModel.id == binding_id,
                BetaSpaceBindingModel.state == "consented",
            )
        )
        return value if isinstance(value, str) else None


def _job(row: BetaMaterialJobModel, chat_id: str) -> BetaMaterialJob:
    return BetaMaterialJob(
        job_id=str(row.id),
        telegram_chat_id=chat_id,
        telegram_file_id=row.telegram_file_id,
        media_kind=row.media_kind,
        sender_label=row.sender_label,
        occurred_at=row.occurred_at,
        file_name=row.file_name,
        mime_type=row.mime_type,
        file_size=row.file_size,
        duration_seconds=row.duration_seconds,
    )


def _material_projection(
    payload: object,
    *,
    expected_chat_id: str,
    expected_kind: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if (
        not isinstance(chat, dict)
        or str(chat.get("id")) != expected_chat_id
        or not isinstance(sender, dict)
        or sender.get("is_bot") is not False
    ):
        return None
    media_kind, media = _select_media(message)
    if media_kind != expected_kind or not isinstance(media, dict):
        return None
    file_id = media.get("file_id")
    occurred = message.get("date")
    if (
        not isinstance(file_id, str)
        or not file_id
        or len(file_id) > 255
        or type(occurred) is not int
    ):
        return None
    first = sender.get("first_name")
    last = sender.get("last_name")
    username = sender.get("username")
    label = " ".join(
        item.strip()
        for item in (first, last)
        if isinstance(item, str) and item.strip()
    )
    if not label and isinstance(username, str) and username:
        label = "@" + username
    if not label:
        label = "Участник"
    file_name = media.get("file_name")
    mime_type = media.get("mime_type")
    file_size = media.get("file_size")
    duration = media.get("duration")
    return {
        "media_kind": media_kind,
        "file_id": file_id,
        "sender_label": label[:128],
        "occurred_at": datetime.fromtimestamp(occurred, tz=UTC),
        "file_name": (
            file_name[:255] if isinstance(file_name, str) and file_name else None
        ),
        "mime_type": (
            mime_type[:255] if isinstance(mime_type, str) and mime_type else None
        ),
        "file_size": file_size if type(file_size) is int and file_size >= 0 else None,
        "duration_seconds": (
            duration if type(duration) is int and duration >= 0 else None
        ),
    }


def _select_media(message: JsonObject) -> tuple[str | None, object]:
    voice = message.get("voice")
    if isinstance(voice, dict):
        return "voice", voice
    document = message.get("document")
    if isinstance(document, dict):
        return "document", document
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [item for item in photos if isinstance(item, dict)]
        if candidates:
            return "photo", max(
                candidates,
                key=lambda item: (
                    item.get("file_size")
                    if type(item.get("file_size")) is int
                    else 0
                ),
            )
    return None, None


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
