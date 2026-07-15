"""Safety tests for exact-target one-use Alpha delivery."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
from app.application.alpha_delivery import (
    AlphaDeliveryReservation,
    AlphaProviderDeliveryError,
    DeliverAlphaGrantCommand,
    DeliverAlphaGrantService,
)
from app.application.alpha_group import AgentSendAttemptState
from app.infrastructure.alpha_delivery import TelegramClientAlphaTextAdapter
from app.services.telegram import TelegramClient


class MemoryDeliveryStore:
    def __init__(self, reservation: AlphaDeliveryReservation) -> None:
        self.current = reservation
        self.finalize_calls = 0

    async def reserve(
        self, command: DeliverAlphaGrantCommand
    ) -> AlphaDeliveryReservation:
        if self.current.state is AgentSendAttemptState.SENDING:
            return self.current
        return AlphaDeliveryReservation(
            attempt_key=self.current.attempt_key,
            state=self.current.state,
            provider_message_id=self.current.provider_message_id,
            error_code=self.current.error_code,
            idempotent_replay=True,
        )

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: AgentSendAttemptState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> AlphaDeliveryReservation:
        self.finalize_calls += 1
        self.current = AlphaDeliveryReservation(
            attempt_key=attempt_key,
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        return self.current


class RecordingTelegram:
    def __init__(
        self,
        *,
        error: AlphaProviderDeliveryError | Exception | None = None,
    ) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def send_text(self, *, chat_id: str, text: str) -> str:
        self.calls.append((chat_id, text))
        if self.error is not None:
            raise self.error
        return "730000001"


def _command() -> DeliverAlphaGrantCommand:
    return DeliverAlphaGrantCommand(
        workspace_key="mark-personal",
        grant_key="opaque-grant",
        now=datetime(2026, 7, 15, tzinfo=UTC),
    )


def _reservation() -> AlphaDeliveryReservation:
    return AlphaDeliveryReservation(
        attempt_key="attempt-00000000000000000000000000000001",
        state=AgentSendAttemptState.SENDING,
        target_chat_id="-100730000001",
        candidate_text="Exact approved candidate",
    )


def test_command_has_no_target_text_business_or_rendering_authority() -> None:
    command = _command()

    assert not hasattr(command, "target_chat_id")
    assert not hasattr(command, "candidate_text")
    assert not hasattr(command, "business_connection_id")
    assert not hasattr(command, "parse_mode")
    assert not hasattr(command, "reply_markup")


def test_success_calls_provider_once_and_terminal_replay_never_sends_again() -> None:
    store = MemoryDeliveryStore(_reservation())
    telegram = RecordingTelegram()
    service = DeliverAlphaGrantService(store=store, telegram=telegram)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.state is AgentSendAttemptState.SENT
    assert first.sent is True
    assert replay.state is AgentSendAttemptState.SENT
    assert replay.idempotent_replay is True
    assert telegram.calls == [
        ("-100730000001", "Exact approved candidate")
    ]
    assert store.finalize_calls == 1


def test_unknown_is_terminal_and_never_automatically_retried() -> None:
    store = MemoryDeliveryStore(_reservation())
    telegram = RecordingTelegram(error=RuntimeError("timeout after write"))
    service = DeliverAlphaGrantService(store=store, telegram=telegram)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.state is AgentSendAttemptState.UNKNOWN
    assert first.retryable is False
    assert replay.state is AgentSendAttemptState.UNKNOWN
    assert len(telegram.calls) == 1


def test_definitive_failure_is_recorded_but_same_attempt_is_not_reused() -> None:
    store = MemoryDeliveryStore(_reservation())
    telegram = RecordingTelegram(
        error=AlphaProviderDeliveryError(
            state=AgentSendAttemptState.FAILED,
            error_code="telegram_api_429",
        )
    )
    service = DeliverAlphaGrantService(store=store, telegram=telegram)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.state is AgentSendAttemptState.FAILED
    assert first.retryable is True
    assert replay.idempotent_replay is True
    assert len(telegram.calls) == 1


def test_literal_telegram_adapter_emits_only_chat_id_and_text() -> None:
    seen_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 42}},
        )

    async def run() -> str:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            telegram = TelegramClient(
                "test-credential",
                api_base_url="https://telegram.invalid",
                http_client=client,
            )
            return await TelegramClientAlphaTextAdapter(telegram).send_text(
                chat_id="-100730000001",
                text="<code>literal</code>\nDraft created: opaque",
            )

    provider_message_id = asyncio.run(run())

    assert provider_message_id == "42"
    assert seen_payloads == [
        {
            "chat_id": "-100730000001",
            "text": "<code>literal</code>\nDraft created: opaque",
        }
    ]
