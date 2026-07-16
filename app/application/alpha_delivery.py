"""One-use exact-target delivery contract for Alpha group replies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.application.alpha_group import AgentSendAttemptState

MAX_TELEGRAM_TEXT_CHARS = 4_096


class AlphaDeliveryError(RuntimeError):
    """A bounded delivery refusal safe for redacted operational reporting."""

    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "alpha_delivery_error"
        super().__init__(self.code)


class AlphaProviderDeliveryError(RuntimeError):
    """A provider outcome with explicit retry semantics."""

    def __init__(self, *, state: AgentSendAttemptState, error_code: str) -> None:
        if state not in {
            AgentSendAttemptState.FAILED,
            AgentSendAttemptState.BLOCKED,
            AgentSendAttemptState.UNKNOWN,
        }:
            raise ValueError("provider delivery error must be failed, blocked or unknown")
        self.state = state
        self.error_code = error_code.strip()[:128] or "alpha_provider_error"
        super().__init__(self.error_code)


@dataclass(frozen=True, slots=True)
class DeliverAlphaGrantCommand:
    """The caller supplies only an opaque grant identity, never target or text."""

    workspace_key: str
    grant_key: str = field(repr=False)
    now: datetime

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.grant_key, "grant_key", 128)
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AlphaDeliveryReservation:
    """DB-derived exact send intent committed before provider I/O."""

    attempt_key: str
    state: AgentSendAttemptState
    target_chat_id: str | None = field(default=None, repr=False)
    candidate_text: str | None = field(default=None, repr=False)
    provider_message_id: str | None = None
    error_code: str | None = None
    idempotent_replay: bool = False

    def __post_init__(self) -> None:
        _require_token(self.attempt_key, "attempt_key", 128)
        if self.state is AgentSendAttemptState.SENDING:
            if self.idempotent_replay:
                raise ValueError("a replay cannot repeat provider delivery")
            if self.target_chat_id is None or self.candidate_text is None:
                raise ValueError("sending reservation requires DB-derived target and text")
            _require_token(self.target_chat_id, "target_chat_id", 255)
            if (
                not self.candidate_text.strip()
                or len(self.candidate_text) > MAX_TELEGRAM_TEXT_CHARS
            ):
                raise ValueError("candidate_text must fit one Telegram text message")
        elif self.target_chat_id is not None or self.candidate_text is not None:
            raise ValueError("terminal delivery result cannot expose target or text")


@dataclass(frozen=True, slots=True)
class AlphaDeliveryResult:
    attempt_key: str
    state: AgentSendAttemptState
    sent: bool
    idempotent_replay: bool
    provider_message_id: str | None = None
    error_code: str | None = None

    @property
    def retryable(self) -> bool:
        """Failed can receive a newly authorized grant; this attempt is never reused."""

        return self.state is AgentSendAttemptState.FAILED


class AlphaDeliveryStore(Protocol):
    async def reserve(
        self, command: DeliverAlphaGrantCommand
    ) -> AlphaDeliveryReservation: ...

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: AgentSendAttemptState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> AlphaDeliveryReservation: ...


class AlphaTelegramTextPort(Protocol):
    """Bot-specific provider boundary with no Business, parse or markup fields."""

    async def send_text(self, *, chat_id: str, text: str) -> str: ...


class DeliverAlphaGrantService:
    """Consume one exact grant and make at most one provider call."""

    def __init__(
        self,
        *,
        store: AlphaDeliveryStore,
        telegram: AlphaTelegramTextPort,
    ) -> None:
        self._store = store
        self._telegram = telegram

    async def execute(self, command: DeliverAlphaGrantCommand) -> AlphaDeliveryResult:
        reservation = await self._store.reserve(command)
        if reservation.state is not AgentSendAttemptState.SENDING:
            return _result(reservation)

        assert reservation.target_chat_id is not None
        assert reservation.candidate_text is not None
        try:
            provider_message_id = await self._telegram.send_text(
                chat_id=reservation.target_chat_id,
                text=reservation.candidate_text,
            )
            if not provider_message_id.strip() or len(provider_message_id) > 255:
                raise AlphaProviderDeliveryError(
                    state=AgentSendAttemptState.UNKNOWN,
                    error_code="alpha_provider_message_id_invalid",
                )
        except AlphaProviderDeliveryError as exc:
            finalized = await self._store.finalize(
                workspace_key=command.workspace_key,
                attempt_key=reservation.attempt_key,
                state=exc.state,
                error_code=exc.error_code,
                finished_at=datetime.now(UTC),
            )
            return _result(finalized)
        except Exception:
            finalized = await self._store.finalize(
                workspace_key=command.workspace_key,
                attempt_key=reservation.attempt_key,
                state=AgentSendAttemptState.UNKNOWN,
                error_code="alpha_provider_outcome_unknown",
                finished_at=datetime.now(UTC),
            )
            return _result(finalized)

        finalized = await self._store.finalize(
            workspace_key=command.workspace_key,
            attempt_key=reservation.attempt_key,
            state=AgentSendAttemptState.SENT,
            provider_message_id=provider_message_id,
            finished_at=datetime.now(UTC),
        )
        return _result(finalized)


def _result(reservation: AlphaDeliveryReservation) -> AlphaDeliveryResult:
    return AlphaDeliveryResult(
        attempt_key=reservation.attempt_key,
        state=reservation.state,
        sent=reservation.state is AgentSendAttemptState.SENT,
        idempotent_replay=reservation.idempotent_replay,
        provider_message_id=reservation.provider_message_id,
        error_code=reservation.error_code,
    )


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded token")
