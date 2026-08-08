"""Universal two-phase processing for Beta voice and document materials."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

JsonObject = dict[str, object]
_MOSCOW_TZ = timezone(timedelta(hours=3))
_MAX_TELEGRAM_TEXT_CHARS = 4_096


class BetaMaterialPhase(StrEnum):
    RECEIPT = "receipt"
    STRUCTURED_RESULT = "structured_result"


class BetaMaterialDisposition(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    DUPLICATE = "duplicate"
    SENT = "sent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BetaMaterialJob:
    """Private source metadata released only after an exact Space lookup."""

    job_id: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    telegram_file_id: str = field(repr=False)
    media_kind: str
    sender_label: str
    occurred_at: datetime
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class BetaMaterialPhaseReservation:
    created: bool
    delivery_id: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    candidate_text: str | None = field(default=None, repr=False)
    resume_from: str = "receipt"


class BetaMaterialStore(Protocol):
    async def reserve_material_receipt(
        self, *, receipt_id: str, now: datetime
    ) -> tuple[BetaMaterialJob, BetaMaterialPhaseReservation] | None: ...

    async def claim_processing(
        self, *, job_id: str, now: datetime
    ) -> BetaMaterialJob | None: ...

    async def persist_result(
        self,
        *,
        job_id: str,
        source_file_sha256: str,
        extracted_text: str,
        structured_text: str,
        now: datetime,
    ) -> bool: ...

    async def settle_processing_failure(
        self, *, job_id: str, error_code: str, now: datetime
    ) -> BetaMaterialPhaseReservation | None: ...

    async def claim_phase(
        self, *, job_id: str, phase: BetaMaterialPhase, now: datetime
    ) -> BetaMaterialPhaseReservation | None: ...

    async def settle_phase(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None: ...


class BetaMaterialTelegram(Protocol):
    async def send_plain_message(self, *, chat_id: str, text: str) -> JsonObject: ...

    async def download_file(self, *, file_id: str) -> bytes: ...


class BetaMaterialExtractor(Protocol):
    async def extract(self, *, job: BetaMaterialJob, content: bytes) -> str: ...


class BetaMaterialStructurer(Protocol):
    async def structure(
        self, *, media_kind: str, extracted_text: str
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class BetaMaterialResult:
    disposition: BetaMaterialDisposition
    code: str


class ProcessBetaMaterial:
    """Persist each irreversible phase before the next provider effect."""

    def __init__(
        self,
        *,
        store: BetaMaterialStore,
        telegram: BetaMaterialTelegram,
        extractor: BetaMaterialExtractor,
        structurer: BetaMaterialStructurer,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._extractor = extractor
        self._structurer = structurer

    async def execute(
        self, *, receipt_id: str, now: datetime
    ) -> BetaMaterialResult:
        reserved = await self._store.reserve_material_receipt(
            receipt_id=receipt_id, now=now
        )
        if reserved is None:
            return BetaMaterialResult(
                BetaMaterialDisposition.NOT_APPLICABLE,
                "beta_material_not_applicable",
            )
        job, receipt = reserved
        if receipt.resume_from == "terminal":
            return BetaMaterialResult(
                BetaMaterialDisposition.DUPLICATE,
                "beta_material_duplicate",
            )
        if receipt.resume_from == "receipt":
            if not receipt.created:
                return BetaMaterialResult(
                    BetaMaterialDisposition.DUPLICATE,
                    "beta_material_duplicate",
                )
            if not await self._send(reservation=receipt, now=now):
                return BetaMaterialResult(
                    BetaMaterialDisposition.UNKNOWN,
                    "beta_material_receipt_unknown",
                )

        if receipt.resume_from == "result":
            result = await self._store.claim_phase(
                job_id=job.job_id,
                phase=BetaMaterialPhase.STRUCTURED_RESULT,
                now=now,
            )
            if result is None:
                return BetaMaterialResult(
                    BetaMaterialDisposition.DUPLICATE,
                    "beta_material_delivery_duplicate",
                )
            if not await self._send(reservation=result, now=now):
                return BetaMaterialResult(
                    BetaMaterialDisposition.UNKNOWN,
                    "beta_material_result_unknown",
                )
            return BetaMaterialResult(
                BetaMaterialDisposition.SENT,
                "beta_material_structured_result_sent",
            )

        claimed = await self._store.claim_processing(job_id=job.job_id, now=now)
        if claimed is None:
            return BetaMaterialResult(
                BetaMaterialDisposition.DUPLICATE,
                "beta_material_processing_duplicate",
            )
        try:
            source = await self._telegram.download_file(
                file_id=claimed.telegram_file_id
            )
            extracted = _bounded_source(
                await self._extractor.extract(job=claimed, content=source)
            )
            structured = _bounded_telegram(
                await self._structurer.structure(
                    media_kind=claimed.media_kind,
                    extracted_text=extracted,
                )
            )
        except Exception as exc:
            failure = await self._store.settle_processing_failure(
                job_id=claimed.job_id,
                error_code=_safe_error_code(exc),
                now=now,
            )
            if failure is not None and await self._send(
                reservation=failure, now=now
            ):
                return BetaMaterialResult(
                    BetaMaterialDisposition.SENT,
                    "beta_material_safe_failure_sent",
                )
            return BetaMaterialResult(
                BetaMaterialDisposition.UNKNOWN,
                "beta_material_processing_unknown",
            )

        if not await self._store.persist_result(
            job_id=claimed.job_id,
            source_file_sha256=hashlib.sha256(source).hexdigest(),
            extracted_text=extracted,
            structured_text=structured,
            now=now,
        ):
            return BetaMaterialResult(
                BetaMaterialDisposition.DUPLICATE,
                "beta_material_result_duplicate",
            )
        result = await self._store.claim_phase(
            job_id=claimed.job_id,
            phase=BetaMaterialPhase.STRUCTURED_RESULT,
            now=now,
        )
        if result is None:
            return BetaMaterialResult(
                BetaMaterialDisposition.DUPLICATE,
                "beta_material_delivery_duplicate",
            )
        if not await self._send(reservation=result, now=now):
            return BetaMaterialResult(
                BetaMaterialDisposition.UNKNOWN,
                "beta_material_result_unknown",
            )
        return BetaMaterialResult(
            BetaMaterialDisposition.SENT,
            "beta_material_structured_result_sent",
        )

    async def _send(
        self, *, reservation: BetaMaterialPhaseReservation, now: datetime
    ) -> bool:
        if (
            reservation.delivery_id is None
            or reservation.telegram_chat_id is None
            or reservation.candidate_text is None
        ):
            raise RuntimeError("beta_material_delivery_invalid")
        try:
            response = await self._telegram.send_plain_message(
                chat_id=reservation.telegram_chat_id,
                text=reservation.candidate_text,
            )
            result = response.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is not int:
                raise RuntimeError("beta_material_delivery_unconfirmed")
        except Exception:
            await self._store.settle_phase(
                delivery_id=reservation.delivery_id,
                outcome="unknown",
                telegram_message_id=None,
                error_code="beta_material_delivery_unknown",
                now=now,
            )
            return False
        await self._store.settle_phase(
            delivery_id=reservation.delivery_id,
            outcome="sent",
            telegram_message_id=str(message_id),
            error_code=None,
            now=now,
        )
        return True


def material_receipt_text(job: BetaMaterialJob) -> str:
    when = job.occurred_at.astimezone(_MOSCOW_TZ).strftime(
        "%d.%m.%Y %H:%M МСК"
    )
    if job.media_kind == "voice":
        duration = (
            f"{job.duration_seconds} с"
            if job.duration_seconds is not None
            else "не подтверждена"
        )
        detail = f"- Длительность: {duration}"
        subject = "голосовое сообщение"
    else:
        subject = "документ" if job.media_kind == "document" else "изображение"
        detail = f"- Файл: {job.file_name or 'без названия'}"
    return "\n".join(
        (
            f"Приняла {subject}:",
            f"- От: {job.sender_label}",
            f"- Время: {when}",
            detail,
            "Извлекаю содержание, структурирую и добавлю результат в память.",
        )
    )


def safe_failure_text(media_kind: str, error_code: str) -> str:
    subject = (
        "голосовое сообщение"
        if media_kind == "voice"
        else "документ или изображение"
    )
    return (
        f"Приняла {subject}, но не смогла надёжно разобрать содержание. "
        f"Код: {error_code}. Исходный факт получения сохранён; "
        "можно отправить файл в другом поддерживаемом формате."
    )


def _safe_error_code(exc: Exception) -> str:
    value = str(exc)
    if value.startswith("beta_material_") and len(value) <= 128:
        return value
    return "beta_material_processing_failed"


def _bounded_source(value: str) -> str:
    result = value.strip()
    if not result:
        raise RuntimeError("beta_material_empty")
    if len(result) > 100_000:
        return result[:99_999].rstrip() + "…"
    return result


def _bounded_telegram(value: str) -> str:
    result = value.strip()
    if not result:
        raise RuntimeError("beta_material_structure_empty")
    if len(result) > _MAX_TELEGRAM_TEXT_CHARS:
        return result[: _MAX_TELEGRAM_TEXT_CHARS - 1].rstrip() + "…"
    return result
