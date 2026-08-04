"""Controlled Slice 2 PROGRAM draft generation and owner review."""

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

CALLBACK_PREFIX = "b2"
CONFIRM_ACTION = "c"
CORRECT_ACTION = "e"
MAX_PROGRAM_CHARS = 12_000
PROGRAM_PLAINTEXT_TTL_DAYS = 30
JsonObject = dict[str, Any]


class ProgramDraftDisposition(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    GENERATED = "generated"
    REPLAYED = "replayed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    CORRECTION_REQUESTED = "correction_requested"
    CORRECTION_ACCEPTED = "correction_accepted"
    APPROVED = "approved"
    APPROVED_DUPLICATE = "approved_duplicate"


@dataclass(frozen=True, slots=True)
class ProgramDraftSource:
    draft_id: str
    transcript_review_id: str
    transcript_version: int
    transcript_digest: str
    transcript_text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProgramDraftJob:
    draft_id: str
    transcript_text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProgramDraftProviderResult:
    program_markdown: str = field(repr=False)
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ProgramDeliveryJob:
    delivery_id: str
    kind: str
    provider_dialog_id: str = field(repr=False)
    version: int
    program_digest: str
    callback_ref: str = field(repr=False)
    program_text: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ProgramReviewResult:
    disposition: ProgramDraftDisposition
    callback_query_id: str | None = field(default=None, repr=False)


class ProgramDraftProvider(Protocol):
    async def generate(self, *, confirmed_transcript: str) -> ProgramDraftProviderResult: ...


class ProgramDraftStore(Protocol):
    async def reserve_from_confirmed_transcript(
        self, *, workspace_key: str, now: datetime
    ) -> ProgramDraftSource | None: ...

    async def claim_provider_call(
        self, *, draft_id: str, now: datetime
    ) -> ProgramDraftJob | None: ...

    async def complete_provider_call(
        self,
        *,
        draft_id: str,
        program_markdown: str,
        input_tokens: int,
        output_tokens: int,
        provider_model: str,
        now: datetime,
    ) -> None: ...

    async def mark_provider_unknown(
        self, *, draft_id: str, error_code: str, now: datetime
    ) -> None: ...

    async def apply_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        provider_dialog_id: str,
        now: datetime,
    ) -> ProgramDraftDisposition: ...

    async def apply_correction(
        self,
        *,
        subject_ref: str,
        provider_dialog_id: str,
        correction_text: str,
        now: datetime,
    ) -> bool: ...

    async def claim_delivery(
        self, *, now: datetime, max_operations: int
    ) -> ProgramDeliveryJob | None: ...

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: datetime,
    ) -> None: ...

    async def purge_expired(self, *, now: datetime) -> int: ...


class ProgramTelegram(Protocol):
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


class ProcessConfirmedTranscriptToProgramDraft:
    """Reserve before the only provider call and fail closed after ambiguity."""

    def __init__(
        self,
        *,
        store: ProgramDraftStore,
        provider: ProgramDraftProvider,
        workspace_key: str,
        provider_model: str,
        enabled: bool,
        provider_max_calls: int,
    ) -> None:
        self._store = store
        self._provider = provider
        self._workspace_key = workspace_key
        self._provider_model = provider_model
        self._enabled = enabled
        self._provider_max_calls = provider_max_calls

    async def run_once(self, *, now: datetime) -> ProgramDraftDisposition:
        if not self._enabled or self._provider_max_calls != 1:
            return ProgramDraftDisposition.DISABLED
        source = await self._store.reserve_from_confirmed_transcript(
            workspace_key=self._workspace_key,
            now=now,
        )
        if source is None:
            return ProgramDraftDisposition.IDLE
        job = await self._store.claim_provider_call(
            draft_id=source.draft_id,
            now=now,
        )
        if job is None:
            return ProgramDraftDisposition.REPLAYED
        try:
            result = await self._provider.generate(
                confirmed_transcript=job.transcript_text
            )
        except Exception as exc:
            await self._store.mark_provider_unknown(
                draft_id=job.draft_id,
                error_code=_safe_error_code(exc),
                now=now,
            )
            return ProgramDraftDisposition.UNKNOWN
        normalized = normalize_program_markdown(result.program_markdown)
        await self._store.complete_provider_call(
            draft_id=job.draft_id,
            program_markdown=normalized,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            provider_model=self._provider_model,
            now=now,
        )
        return ProgramDraftDisposition.GENERATED


class ProcessPilotProgramReviewUpdate:
    """Apply exact owner/chat-bound program callbacks and corrections."""

    def __init__(
        self,
        *,
        store: ProgramDraftStore,
        telegram: ProgramTelegram,
        identity_pepper: str,
        enabled: bool,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._pepper = identity_pepper
        self._enabled = enabled

    async def execute(self, *, update: JsonObject, now: datetime) -> ProgramReviewResult:
        event = extract_program_review_update(update, pepper=self._pepper)
        if event is None:
            return ProgramReviewResult(ProgramDraftDisposition.NOT_APPLICABLE)
        if not self._enabled:
            raise BecomeControlledIntakeError("become_pilot_slice2_disabled")
        action, subject_ref, dialog_id, callback_ref, callback_query_id, text = event
        if action == "correction":
            assert text is not None
            handled = await self._store.apply_correction(
                subject_ref=subject_ref,
                provider_dialog_id=dialog_id,
                correction_text=text,
                now=now,
            )
            return ProgramReviewResult(
                ProgramDraftDisposition.CORRECTION_ACCEPTED
                if handled
                else ProgramDraftDisposition.NOT_APPLICABLE
            )
        assert callback_ref is not None and callback_query_id is not None
        disposition = await self._store.apply_callback(
            action=action,
            callback_ref=callback_ref,
            subject_ref=subject_ref,
            provider_dialog_id=dialog_id,
            now=now,
        )
        callback_text = {
            ProgramDraftDisposition.CORRECTION_REQUESTED: (
                "Отправьте исправленный черновик программы одним сообщением."
            ),
            ProgramDraftDisposition.APPROVED: "Черновик программы подтверждён.",
            ProgramDraftDisposition.APPROVED_DUPLICATE: (
                "Эта версия программы уже подтверждена."
            ),
        }.get(disposition)
        await self._telegram.answer_callback_query(
            callback_query_id=callback_query_id,
            text=callback_text,
            show_alert=False,
        )
        return ProgramReviewResult(disposition, callback_query_id)


class DeliverPilotProgramDraft:
    """Deliver one owner-only draft operation; ambiguity is never auto-retried."""

    def __init__(
        self,
        *,
        store: ProgramDraftStore,
        telegram: ProgramTelegram,
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
            now=now, max_operations=self._max_operations
        )
        if job is None:
            return "idle"
        text, markup = program_delivery_body(job)
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


def normalize_program_markdown(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_PROGRAM_CHARS:
        raise BecomeControlledIntakeError("become_program_draft_invalid")
    return normalized


def program_digest(value: str) -> str:
    return hashlib.sha256(normalize_program_markdown(value).encode()).hexdigest()


def extract_program_review_update(
    update: JsonObject,
    *,
    pepper: str,
) -> tuple[str, str, str, str | None, str | None, str | None] | None:
    if type(update.get("update_id")) is not int:
        raise BecomeControlledIntakeError("become_program_update_id_invalid")
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        action, callback_ref = parse_program_callback_data(callback.get("data"))
        subject_ref, dialog_id = _actor(
            callback.get("from"), callback.get("message"), pepper
        )
        callback_id = callback.get("id")
        if not isinstance(callback_id, str) or not callback_id:
            raise BecomeControlledIntakeError(
                "become_program_callback_query_id_invalid"
            )
        return action, subject_ref, dialog_id, callback_ref, callback_id, None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or text.lstrip().startswith("/"):
        return None
    normalized = text.strip()
    if not normalized:
        return None
    normalized = normalize_program_markdown(normalized)
    subject_ref, dialog_id = _actor(message.get("from"), message, pepper)
    return "correction", subject_ref, dialog_id, None, None, normalized


def parse_program_callback_data(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise BecomeControlledIntakeError("become_program_callback_data_invalid")
    parts = value.split(":")
    if (
        len(parts) != 3
        or parts[0] != CALLBACK_PREFIX
        or parts[1] not in {CONFIRM_ACTION, CORRECT_ACTION}
        or len(parts[2]) != 32
    ):
        raise BecomeControlledIntakeError("become_program_callback_data_invalid")
    try:
        int(parts[2], 16)
    except ValueError:
        raise BecomeControlledIntakeError(
            "become_program_callback_data_invalid"
        ) from None
    return parts[1], parts[2]


def program_delivery_body(
    job: ProgramDeliveryJob,
) -> tuple[str, dict[str, Any] | None]:
    if job.kind in {"presentation", "redisplay"}:
        if not job.program_text:
            raise BecomeControlledIntakeError("become_program_content_missing")
        return (
            "Черновик программы:\n\n" + job.program_text,
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
        return ("Отправьте исправленный черновик программы одним сообщением.", None)
    if job.kind == "confirmation":
        return ("Программа подтверждена и сохранена как внутренний результат.", None)
    raise BecomeControlledIntakeError("become_program_delivery_kind_invalid")


def program_delivery_request_digest(
    *,
    kind: str,
    version: int,
    digest: str,
    callback_ref: str,
) -> str:
    return hashlib.sha256(
        f"{kind}:{version}:{digest}:{callback_ref}".encode()
    ).hexdigest()


def _actor(
    actor_value: object,
    message_value: object,
    pepper: str,
) -> tuple[str, str]:
    if not isinstance(actor_value, dict) or actor_value.get("is_bot") is not False:
        raise BecomeControlledIntakeError("become_program_actor_invalid")
    if not isinstance(message_value, dict):
        raise BecomeControlledIntakeError("become_program_message_missing")
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
        raise BecomeControlledIntakeError("become_program_actor_or_chat_invalid")
    return (
        opaque_subject_ref(provider_actor_id=str(actor_id), pepper=pepper),
        str(chat_id),
    )


def _safe_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", exc.__class__.__name__)
    return str(code).strip()[:128] or "become_program_provider_unknown"
