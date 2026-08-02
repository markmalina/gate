from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.application.become_controlled_intake import BecomeControlledIntakeError
from app.application.become_media_prompt import (
    BECOME_MEDIA_PROMPT_TEXT,
    BecomeMediaPromptCandidate,
    BecomeMediaPromptProviderError,
    BecomeMediaPromptReservation,
    BecomeMediaPromptState,
    DeliverBecomeMediaPromptService,
)


NOW = datetime(2026, 8, 2, tzinfo=UTC)


class FakeStore:
    def __init__(
        self,
        *,
        candidate: BecomeMediaPromptCandidate | None,
        reservation: BecomeMediaPromptReservation | None = None,
    ) -> None:
        self.next_candidate = candidate
        self.next_reservation = reservation
        self.finalized: list[dict[str, object]] = []

    async def candidate(
        self,
        *,
        workspace_key: str,
    ) -> BecomeMediaPromptCandidate | None:
        assert workspace_key == "workspace-test"
        return self.next_candidate

    async def reserve(
        self,
        *,
        workspace_key: str,
        candidate_key: str,
        now: datetime,
    ) -> BecomeMediaPromptReservation:
        assert workspace_key == "workspace-test"
        assert candidate_key == "candidate-1"
        assert now == NOW
        assert self.next_reservation is not None
        return self.next_reservation

    async def finalize(self, **values: object) -> BecomeMediaPromptReservation:
        self.finalized.append(values)
        state = values["state"]
        assert isinstance(state, BecomeMediaPromptState)
        return BecomeMediaPromptReservation(
            attempt_key=str(values["attempt_key"]),
            state=state,
            provider_message_id=(
                str(values["provider_message_id"])
                if values.get("provider_message_id") is not None
                else None
            ),
            error_code=(
                str(values["error_code"])
                if values.get("error_code") is not None
                else None
            ),
        )


class FakePresence:
    def __init__(self, count: int | None = 2) -> None:
        self.count = count

    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None:
        assert installation_ref == "installation-1"
        assert provider_dialog_id == "private-chat"
        return self.count


class FakeTelegram:
    def __init__(
        self,
        *,
        error: BecomeMediaPromptProviderError | None = None,
    ) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def send_text(self, *, chat_id: str, text: str) -> str:
        self.calls.append((chat_id, text))
        if self.error is not None:
            raise self.error
        return "provider-message-1"


def candidate() -> BecomeMediaPromptCandidate:
    return BecomeMediaPromptCandidate(
        candidate_key="candidate-1",
        installation_ref="installation-1",
        target_chat_id="private-chat",
    )


def sending() -> BecomeMediaPromptReservation:
    return BecomeMediaPromptReservation(
        attempt_key="become-prompt-00000000000000000000000000000000",
        state=BecomeMediaPromptState.SENDING,
        target_chat_id="private-chat",
        prompt_text=BECOME_MEDIA_PROMPT_TEXT,
    )


@pytest.mark.asyncio
async def test_no_candidate_has_no_external_effect() -> None:
    store = FakeStore(candidate=None)
    telegram = FakeTelegram()
    result = await DeliverBecomeMediaPromptService(
        store=store,
        presence=FakePresence(),
        telegram=telegram,
    ).execute(workspace_key="workspace-test", now=NOW)
    assert result.state is None
    assert result.sent is False
    assert telegram.calls == []
    assert store.finalized == []


@pytest.mark.asyncio
async def test_owner_only_candidate_sends_and_finalizes_once() -> None:
    store = FakeStore(candidate=candidate(), reservation=sending())
    telegram = FakeTelegram()
    result = await DeliverBecomeMediaPromptService(
        store=store,
        presence=FakePresence(),
        telegram=telegram,
    ).execute(workspace_key="workspace-test", now=NOW)
    assert result.state is BecomeMediaPromptState.SENT
    assert result.sent is True
    assert telegram.calls == [
        ("private-chat", BECOME_MEDIA_PROMPT_TEXT)
    ]
    assert store.finalized[0]["provider_message_id"] == "provider-message-1"
    assert store.finalized[0]["state"] is BecomeMediaPromptState.SENT


@pytest.mark.asyncio
async def test_non_owner_only_candidate_fails_before_reservation() -> None:
    store = FakeStore(candidate=candidate(), reservation=sending())
    telegram = FakeTelegram()
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_prompt_owner_only_required",
    ):
        await DeliverBecomeMediaPromptService(
            store=store,
            presence=FakePresence(3),
            telegram=telegram,
        ).execute(workspace_key="workspace-test", now=NOW)
    assert telegram.calls == []
    assert store.finalized == []


@pytest.mark.asyncio
async def test_terminal_replay_never_sends_again() -> None:
    store = FakeStore(
        candidate=candidate(),
        reservation=BecomeMediaPromptReservation(
            attempt_key="become-prompt-00000000000000000000000000000000",
            state=BecomeMediaPromptState.SENT,
            provider_message_id="provider-message-1",
            idempotent_replay=True,
        ),
    )
    telegram = FakeTelegram()
    result = await DeliverBecomeMediaPromptService(
        store=store,
        presence=FakePresence(),
        telegram=telegram,
    ).execute(workspace_key="workspace-test", now=NOW)
    assert result.sent is True
    assert result.duplicate is True
    assert telegram.calls == []
    assert store.finalized == []


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_is_durable_unknown() -> None:
    store = FakeStore(candidate=candidate(), reservation=sending())
    telegram = FakeTelegram(
        error=BecomeMediaPromptProviderError(
            state=BecomeMediaPromptState.UNKNOWN,
            error_code="telegram_outcome_unknown",
        )
    )
    result = await DeliverBecomeMediaPromptService(
        store=store,
        presence=FakePresence(),
        telegram=telegram,
    ).execute(workspace_key="workspace-test", now=NOW)
    assert result.state is BecomeMediaPromptState.UNKNOWN
    assert result.sent is False
    assert result.error_code == "telegram_outcome_unknown"
    assert store.finalized[0]["provider_message_id"] is None
