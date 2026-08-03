"""Human review for the one-workspace BECOME Pilot transcript slice."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
    opaque_subject_ref,
)

CALLBACK_PREFIX = "b1"
CONFIRM_ACTION = "c"
CORRECT_ACTION = "e"
MAX_CORRECTION_CHARS = 12_000
JsonObject = dict[str, Any]


class PilotReviewDisposition(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    CORRECTION_ACCEPTED = "correction_accepted"
    CORRECTION_REQUESTED = "correction_requested"
    APPROVED = "approved"
    APPROVED_DUPLICATE = "approved_duplicate"


@dataclass(frozen=True, slots=True)
class PilotActor:
    subject_ref: str
    provider_dialog_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PilotReviewUpdate:
    action: str
    actor: PilotActor
    callback_ref: str | None = field(default=None, repr=False)
    callback_query_id: str | None = field(default=None, repr=False)
    correction_text: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PilotReviewUpdateResult:
    disposition: PilotReviewDisposition
    callback_query_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PilotDeliveryJob:
    delivery_id: str
    kind: str
    provider_dialog_id: str = field(repr=False)
    version: int
    transcript_digest: str
    callback_ref: str = field(repr=False)
    transcript_text: str | None = field(default=None, repr=False)
    transcript_source: str | None = field(default=None, repr=False)


class PilotTranscriptReviewStore(Protocol):
    async def apply_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        provider_dialog_id: str,
        now: datetime,
    ) -> PilotReviewDisposition: ...

    async def apply_correction(
        self,
        *,
        subject_ref: str,
        provider_dialog_id: str,
        correction_text: str,
        now: datetime,
    ) -> bool: ...

    async def claim_delivery(
        self,
        *,
        now: datetime,
        max_operations: int,
    ) -> PilotDeliveryJob | None: ...

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: datetime,
    ) -> None: ...

    async def tombstone_receipt(
        self,
        *,
        receipt_id: str,
        now: datetime,
    ) -> None: ...


class PilotTelegram(Protocol):
    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]: ...


class ProcessPilotTranscriptReviewUpdate:
    """Apply exact actor/chat-bound callbacks and corrected text."""

    def __init__(
        self,
        *,
        store: PilotTranscriptReviewStore,
        telegram: PilotTelegram,
        identity_pepper: str,
        enabled: bool,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._pepper = identity_pepper
        self._enabled = enabled

    async def execute(
        self,
        *,
        update: JsonObject,
        now: datetime,
    ) -> PilotReviewUpdateResult:
        event = extract_pilot_review_update(update, pepper=self._pepper)
        if event is None:
            return PilotReviewUpdateResult(PilotReviewDisposition.NOT_APPLICABLE)
        if not self._enabled:
            raise BecomeControlledIntakeError("become_pilot_slice1_disabled")
        if event.action == "correction":
            if event.correction_text is None:
                raise BecomeControlledIntakeError(
                    "become_pilot_correction_missing"
                )
            handled = await self._store.apply_correction(
                subject_ref=event.actor.subject_ref,
                provider_dialog_id=event.actor.provider_dialog_id,
                correction_text=event.correction_text,
                now=now,
            )
            return PilotReviewUpdateResult(
                PilotReviewDisposition.CORRECTION_ACCEPTED
                if handled
                else PilotReviewDisposition.NOT_APPLICABLE
            )
        if event.callback_ref is None or event.callback_query_id is None:
            raise BecomeControlledIntakeError("become_pilot_callback_invalid")
        disposition = await self._store.apply_callback(
            action=event.action,
            callback_ref=event.callback_ref,
            subject_ref=event.actor.subject_ref,
            provider_dialog_id=event.actor.provider_dialog_id,
            now=now,
        )
        callback_text = {
            PilotReviewDisposition.CORRECTION_REQUESTED: (
                "Отправьте исправленный текст одним сообщением."
            ),
            PilotReviewDisposition.APPROVED: "Текст подтверждён.",
            PilotReviewDisposition.APPROVED_DUPLICATE: (
                "Этот текст уже подтверждён."
            ),
        }.get(disposition)
        await self._telegram.answer_callback_query(
            callback_query_id=event.callback_query_id,
            text=callback_text,
            show_alert=False,
        )
        return PilotReviewUpdateResult(
            disposition,
            callback_query_id=event.callback_query_id,
        )

    async def tombstone_receipt(
        self,
        *,
        receipt_id: str,
        now: datetime,
    ) -> None:
        await self._store.tombstone_receipt(
            receipt_id=receipt_id,
            now=now,
        )


class DeliverPilotTranscriptReview:
    """Send at most one reserved Pilot message and never blind-retry unknown."""

    def __init__(
        self,
        *,
        store: PilotTranscriptReviewStore,
        telegram: PilotTelegram,
        enabled: bool,
        max_operations: int,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._enabled = enabled
        self._max_operations = max_operations

    async def run_once(self, *, now: datetime) -> str:
        if not self._enabled or self._max_operations < 1:
            return "disabled"
        job = await self._store.claim_delivery(
            now=now,
            max_operations=self._max_operations,
        )
        if job is None:
            return "idle"
        text, markup = delivery_body(job)
        try:
            response = await self._telegram.send_message(
                chat_id=job.provider_dialog_id,
                text=text,
                reply_markup=markup,
            )
        except Exception:
            await self._store.settle_delivery(
                delivery_id=job.delivery_id,
                outcome="unknown",
                telegram_message_id=None,
                now=now,
            )
            return "unknown"
        message_id = response.get("result", {}).get("message_id")
        if not isinstance(message_id, int):
            await self._store.settle_delivery(
                delivery_id=job.delivery_id,
                outcome="unknown",
                telegram_message_id=None,
                now=now,
            )
            return "unknown"
        await self._store.settle_delivery(
            delivery_id=job.delivery_id,
            outcome="sent",
            telegram_message_id=str(message_id),
            now=now,
        )
        return "sent"


def extract_pilot_review_update(
    update: JsonObject,
    *,
    pepper: str,
) -> PilotReviewUpdate | None:
    if type(update.get("update_id")) is not int:
        raise BecomeControlledIntakeError("become_pilot_update_id_invalid")
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        actor = _actor(callback.get("from"), callback.get("message"), pepper)
        callback_id = callback.get("id")
        action, callback_ref = parse_callback_data(callback.get("data"))
        if not isinstance(callback_id, str) or not callback_id:
            raise BecomeControlledIntakeError(
                "become_pilot_callback_query_id_invalid"
            )
        return PilotReviewUpdate(
            action=action,
            actor=actor,
            callback_ref=callback_ref,
            callback_query_id=callback_id,
        )
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if not normalized or normalized.startswith("/"):
        return None
    if len(normalized) > MAX_CORRECTION_CHARS:
        raise BecomeControlledIntakeError(
            "become_pilot_correction_too_large"
        )
    actor = _actor(message.get("from"), message, pepper)
    return PilotReviewUpdate(
        action="correction",
        actor=actor,
        correction_text=normalized,
    )


def parse_callback_data(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise BecomeControlledIntakeError("become_pilot_callback_data_invalid")
    parts = value.split(":")
    if (
        len(parts) != 3
        or parts[0] != CALLBACK_PREFIX
        or parts[1] not in {CONFIRM_ACTION, CORRECT_ACTION}
        or len(parts[2]) != 32
    ):
        raise BecomeControlledIntakeError("become_pilot_callback_data_invalid")
    try:
        int(parts[2], 16)
    except ValueError:
        raise BecomeControlledIntakeError(
            "become_pilot_callback_data_invalid"
        ) from None
    return parts[1], parts[2]


def delivery_body(
    job: PilotDeliveryJob,
) -> tuple[str, dict[str, Any] | None]:
    if job.kind in {"presentation", "redisplay"}:
        if not job.transcript_text:
            raise BecomeControlledIntakeError(
                "become_pilot_transcript_content_missing"
            )
        if job.transcript_source == "human_correction":
            body = (
                "Вот текст:\n\n"
                + job.transcript_text
                + "\n\nПодтвердите отправку."
            )
        else:
            body = "Вот распознанный текст:\n\n" + job.transcript_text
        return (
            body,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "Подтвердить",
                            "callback_data": (
                                f"{CALLBACK_PREFIX}:{CONFIRM_ACTION}:"
                                f"{job.callback_ref}"
                            ),
                        },
                        {
                            "text": "Исправить",
                            "callback_data": (
                                f"{CALLBACK_PREFIX}:{CORRECT_ACTION}:"
                                f"{job.callback_ref}"
                            ),
                        },
                    ]
                ]
            },
        )
    if job.kind == "correction_prompt":
        return ("Отправьте исправленный текст одним сообщением.", None)
    if job.kind == "confirmation":
        return ("Текст подтверждён и сохранён.", None)
    raise BecomeControlledIntakeError("become_pilot_delivery_kind_invalid")


def request_digest(
    *,
    kind: str,
    version: int,
    transcript_digest: str,
    callback_ref: str,
    attempt_no: int = 1,
) -> str:
    return hashlib.sha256(
        (
            f"{kind}:{version}:{transcript_digest}:"
            f"{callback_ref}:{attempt_no}"
        ).encode()
    ).hexdigest()


def _actor(
    actor_value: object,
    message_value: object,
    pepper: str,
) -> PilotActor:
    if not isinstance(actor_value, dict) or actor_value.get("is_bot") is not False:
        raise BecomeControlledIntakeError("become_pilot_actor_invalid")
    if not isinstance(message_value, dict):
        raise BecomeControlledIntakeError("become_pilot_message_missing")
    chat = message_value.get("chat")
    actor_id = actor_value.get("id")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    chat_type = chat.get("type") if isinstance(chat, dict) else None
    if (
        chat_type not in {"group", "supergroup"}
        or not isinstance(actor_id, (str, int))
        or isinstance(actor_id, bool)
        or not isinstance(chat_id, (str, int))
        or isinstance(chat_id, bool)
    ):
        raise BecomeControlledIntakeError(
            "become_pilot_actor_or_chat_invalid"
        )
    return PilotActor(
        subject_ref=opaque_subject_ref(
            provider_actor_id=str(actor_id),
            pepper=pepper,
        ),
        provider_dialog_id=str(chat_id),
    )
