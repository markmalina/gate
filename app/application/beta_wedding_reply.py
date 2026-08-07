"""Bounded one-reply orchestration for the owner-only Wedding Beta Space."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

JsonObject = dict[str, Any]
_MAX_TELEGRAM_TEXT_CHARS = 4_096


class BetaWeddingReplyDisposition(StrEnum):
    DISABLED = "disabled"
    NOT_APPLICABLE = "not_applicable"
    DUPLICATE = "duplicate"
    SENT = "sent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BetaWeddingContextMessage:
    sender_label: str
    text: str


@dataclass(frozen=True, slots=True)
class BetaWeddingReplyReservation:
    created: bool
    reply_id: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    media_kind: str | None = None
    sender_label: str | None = None
    current_text: str | None = field(default=None, repr=False)
    context: tuple[BetaWeddingContextMessage, ...] = field(
        default=(), repr=False
    )


class BetaWeddingReplyStore(Protocol):
    async def reserve_reply(
        self, *, receipt_id: str, now: datetime
    ) -> BetaWeddingReplyReservation | None: ...

    async def stage_candidate(
        self,
        *,
        reply_id: str,
        text: str,
        result_code: str,
        now: datetime,
    ) -> bool: ...

    async def settle_reply(
        self,
        *,
        reply_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None: ...


class BetaWeddingReplyModel(Protocol):
    async def propose(
        self,
        *,
        current: BetaWeddingContextMessage,
        context: tuple[BetaWeddingContextMessage, ...],
    ) -> str: ...


class BetaWeddingReplyTelegram(Protocol):
    async def send_plain_message(
        self, *, chat_id: str, text: str
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class BetaWeddingReplyResult:
    disposition: BetaWeddingReplyDisposition
    result_code: str


class RespondToBetaWeddingMessage:
    """Create at most one model proposal and one send for one accepted event."""

    def __init__(
        self,
        *,
        store: BetaWeddingReplyStore,
        model: BetaWeddingReplyModel,
        telegram: BetaWeddingReplyTelegram,
        enabled: bool,
    ) -> None:
        self._store = store
        self._model = model
        self._telegram = telegram
        self._enabled = enabled

    async def execute(
        self, *, receipt_id: str, now: datetime
    ) -> BetaWeddingReplyResult:
        if not self._enabled:
            return BetaWeddingReplyResult(
                BetaWeddingReplyDisposition.DISABLED,
                "beta_wedding_reply_disabled",
            )
        reservation = await self._store.reserve_reply(
            receipt_id=receipt_id, now=now
        )
        if reservation is None:
            return BetaWeddingReplyResult(
                BetaWeddingReplyDisposition.NOT_APPLICABLE,
                "beta_wedding_reply_not_applicable",
            )
        if not reservation.created:
            return BetaWeddingReplyResult(
                BetaWeddingReplyDisposition.DUPLICATE,
                "beta_wedding_reply_duplicate",
            )
        if (
            reservation.reply_id is None
            or reservation.telegram_chat_id is None
            or reservation.media_kind is None
            or reservation.sender_label is None
        ):
            raise RuntimeError("beta_wedding_reply_reservation_invalid")

        current = BetaWeddingContextMessage(
            sender_label=reservation.sender_label,
            text=reservation.current_text or "",
        )
        if reservation.media_kind == "text" and current.text.strip():
            try:
                candidate = await self._model.propose(
                    current=current,
                    context=reservation.context,
                )
                result_code = "beta_wedding_model_reply"
            except Exception:
                candidate = _text_fallback(current.text)
                result_code = "beta_wedding_model_fallback"
        else:
            candidate = _media_acknowledgement(reservation.media_kind)
            result_code = "beta_wedding_media_acknowledgement"

        candidate = _bounded_candidate(candidate)
        staged = await self._store.stage_candidate(
            reply_id=reservation.reply_id,
            text=candidate,
            result_code=result_code,
            now=now,
        )
        if not staged:
            return BetaWeddingReplyResult(
                BetaWeddingReplyDisposition.DUPLICATE,
                "beta_wedding_reply_stage_duplicate",
            )

        try:
            response = await self._telegram.send_plain_message(
                chat_id=reservation.telegram_chat_id,
                text=candidate,
            )
            provider_message_id = response.get("result", {}).get("message_id")
            if type(provider_message_id) is not int:
                raise RuntimeError("beta_wedding_message_id_unconfirmed")
        except Exception:
            await self._store.settle_reply(
                reply_id=reservation.reply_id,
                outcome="unknown",
                telegram_message_id=None,
                error_code="beta_wedding_delivery_unknown",
                now=now,
            )
            return BetaWeddingReplyResult(
                BetaWeddingReplyDisposition.UNKNOWN,
                "beta_wedding_delivery_unknown",
            )

        await self._store.settle_reply(
            reply_id=reservation.reply_id,
            outcome="sent",
            telegram_message_id=str(provider_message_id),
            error_code=None,
            now=now,
        )
        return BetaWeddingReplyResult(
            BetaWeddingReplyDisposition.SENT,
            result_code,
        )


def _bounded_candidate(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise RuntimeError("beta_wedding_reply_empty")
    if len(candidate) > _MAX_TELEGRAM_TEXT_CHARS:
        candidate = candidate[: _MAX_TELEGRAM_TEXT_CHARS - 1].rstrip() + "…"
    return candidate


def _text_fallback(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) > 320:
        compact = compact[:319].rstrip() + "…"
    return (
        "Приняла и сохранила в общей памяти. "
        f"Я поняла сообщение так: {compact}"
    )


def _media_acknowledgement(media_kind: str) -> str:
    labels = {
        "voice": "голосовое сообщение",
        "photo": "фотографию",
        "document": "документ",
    }
    label = labels.get(media_kind, "материал")
    return (
        f"Приняла {label} и сохранила его в общей памяти. "
        "Сейчас я зафиксировала сам материал; понимание его содержания "
        "потребует отдельной обработки."
    )
