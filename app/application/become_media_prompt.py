"""One-shot BECOME-only Telegram media prompt after durable consent gates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)

BECOME_MEDIA_PROMPT_SCHEMA = "gate.become-media-prompt@1"
BECOME_MEDIA_PROMPT_TEXT = (
    "BECOME готов принять голос. Ответьте на это сообщение одним коротким "
    "голосовым сообщением (до 20 МБ). Команды, регистрацию и согласие "
    "повторять не нужно."
)


class BecomeMediaPromptState(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BecomeMediaPromptCandidate:
    candidate_key: str
    installation_ref: str
    target_chat_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BecomeMediaPromptReservation:
    attempt_key: str
    state: BecomeMediaPromptState
    target_chat_id: str | None = field(default=None, repr=False)
    prompt_text: str | None = field(default=None, repr=False)
    provider_message_id: str | None = field(default=None, repr=False)
    error_code: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class BecomeMediaPromptResult:
    state: BecomeMediaPromptState | None
    sent: bool
    duplicate: bool
    error_code: str | None = None


class BecomeMediaPromptStore(Protocol):
    async def candidate(
        self,
        *,
        workspace_key: str,
    ) -> BecomeMediaPromptCandidate | None: ...

    async def reserve(
        self,
        *,
        workspace_key: str,
        candidate_key: str,
        now: datetime,
    ) -> BecomeMediaPromptReservation: ...

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: BecomeMediaPromptState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> BecomeMediaPromptReservation: ...


class BecomeMediaPromptPresence(Protocol):
    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None: ...


class BecomeMediaPromptTelegram(Protocol):
    async def send_text(self, *, chat_id: str, text: str) -> str: ...


class BecomeMediaPromptProviderError(RuntimeError):
    def __init__(
        self,
        *,
        state: BecomeMediaPromptState,
        error_code: str,
    ) -> None:
        self.state = state
        self.error_code = error_code
        super().__init__(error_code)


class DeliverBecomeMediaPromptService:
    """Send exactly one reply anchor; never retry an ambiguous provider outcome."""

    def __init__(
        self,
        *,
        store: BecomeMediaPromptStore,
        presence: BecomeMediaPromptPresence,
        telegram: BecomeMediaPromptTelegram,
    ) -> None:
        self._store = store
        self._presence = presence
        self._telegram = telegram

    async def execute(
        self,
        *,
        workspace_key: str,
        now: datetime,
    ) -> BecomeMediaPromptResult:
        if now.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_media_prompt_timestamp_invalid"
            )
        candidate = await self._store.candidate(
            workspace_key=workspace_key,
        )
        if candidate is None:
            return BecomeMediaPromptResult(
                state=None,
                sent=False,
                duplicate=False,
            )
        member_count = await self._presence.member_count(
            installation_ref=candidate.installation_ref,
            provider_dialog_id=candidate.target_chat_id,
        )
        if member_count != 2:
            raise BecomeControlledIntakeError(
                "become_media_prompt_owner_only_required"
            )
        reservation = await self._store.reserve(
            workspace_key=workspace_key,
            candidate_key=candidate.candidate_key,
            now=now,
        )
        if reservation.state is not BecomeMediaPromptState.SENDING:
            return BecomeMediaPromptResult(
                state=reservation.state,
                sent=reservation.state is BecomeMediaPromptState.SENT,
                duplicate=reservation.idempotent_replay,
                error_code=reservation.error_code,
            )
        if (
            reservation.target_chat_id is None
            or reservation.prompt_text != BECOME_MEDIA_PROMPT_TEXT
        ):
            raise BecomeControlledIntakeError(
                "become_media_prompt_reservation_invalid"
            )
        try:
            provider_message_id = await self._telegram.send_text(
                chat_id=reservation.target_chat_id,
                text=reservation.prompt_text,
            )
        except BecomeMediaPromptProviderError as exc:
            terminal = await self._store.finalize(
                workspace_key=workspace_key,
                attempt_key=reservation.attempt_key,
                state=exc.state,
                finished_at=now,
                error_code=exc.error_code,
            )
        else:
            terminal = await self._store.finalize(
                workspace_key=workspace_key,
                attempt_key=reservation.attempt_key,
                state=BecomeMediaPromptState.SENT,
                finished_at=now,
                provider_message_id=provider_message_id,
            )
        return BecomeMediaPromptResult(
            state=terminal.state,
            sent=terminal.state is BecomeMediaPromptState.SENT,
            duplicate=terminal.idempotent_replay,
            error_code=terminal.error_code,
        )


def become_media_prompt_digest() -> str:
    return hashlib.sha256(
        BECOME_MEDIA_PROMPT_TEXT.encode("utf-8")
    ).hexdigest()
