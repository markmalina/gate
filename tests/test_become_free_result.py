from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)
from app.application.become_free_result import (
    BecomeFreeResultCandidate,
    BecomeFreeResultDeliveryResult,
    BecomeFreeResultProviderError,
    BecomeFreeResultReservation,
    BecomeFreeResultState,
    DeliverBecomeFreeResultService,
    build_artifact_receipt,
    build_delivery_receipt,
    build_free_result_text,
    canonical_digest,
    result_digest,
    validate_receipt_replay,
)


CORRELATION = "synthetic://correlation/voice/one"
TAKE_DIGEST = "1" * 64
TRANSCRIPT_DIGEST = "2" * 64
DESTINATION_DIGEST = "3" * 64


class FakePresence:
    def __init__(self, count: int | None = 2) -> None:
        self.count = count

    async def member_count(self, **_kwargs: object) -> int | None:
        return self.count


class FakeTelegram:
    def __init__(self, *, unknown: bool = False) -> None:
        self.calls = 0
        self.unknown = unknown

    async def send_text(self, *, chat_id: str, text: str) -> str:
        assert chat_id == "private-group"
        assert result_digest(text) == STORE.candidate_value.result_digest
        self.calls += 1
        if self.unknown:
            raise BecomeFreeResultProviderError(
                state=BecomeFreeResultState.UNKNOWN,
                error_code="telegram_outcome_unknown",
            )
        return "private-provider-id"


class FakeStore:
    def __init__(self) -> None:
        text = build_free_result_text("Подтверждённый ответ")
        self.candidate_value = BecomeFreeResultCandidate(
            candidate_key="candidate",
            correlation_ref=CORRELATION,
            result_digest=result_digest(text),
            take_receipt_digest=TAKE_DIGEST,
            destination_binding_digest=DESTINATION_DIGEST,
            installation_ref="installation",
            target_chat_id="private-group",
        )
        self.text = text
        self.state: BecomeFreeResultState | None = None
        self.reserve_calls = 0

    async def candidate(
        self,
        **_kwargs: object,
    ) -> BecomeFreeResultCandidate:
        return self.candidate_value

    async def reserve(
        self,
        **_kwargs: object,
    ) -> BecomeFreeResultReservation:
        self.reserve_calls += 1
        if self.state is not None:
            return BecomeFreeResultReservation(
                attempt_key="attempt",
                state=self.state,
                error_code=(
                    "prior_outcome_unknown"
                    if self.state is BecomeFreeResultState.UNKNOWN
                    else None
                ),
                provider_message_id=(
                    "private-provider-id"
                    if self.state is BecomeFreeResultState.SENT
                    else None
                ),
                idempotent_replay=True,
            )
        self.state = BecomeFreeResultState.SENDING
        return BecomeFreeResultReservation(
            attempt_key="attempt",
            state=BecomeFreeResultState.SENDING,
            target_chat_id="private-group",
            result_text=self.text,
        )

    async def finalize(
        self,
        *,
        state: BecomeFreeResultState,
        provider_message_id: str | None = None,
        error_code: str | None = None,
        **_kwargs: object,
    ) -> BecomeFreeResultReservation:
        self.state = state
        return BecomeFreeResultReservation(
            attempt_key="attempt",
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )


STORE = FakeStore()


def _service(
    *,
    store: FakeStore,
    telegram: FakeTelegram,
    presence: FakePresence | None = None,
    enabled: bool = True,
    max_calls: int = 1,
) -> DeliverBecomeFreeResultService:
    return DeliverBecomeFreeResultService(
        store=store,
        presence=presence or FakePresence(),
        telegram=telegram,
        enabled=enabled,
        max_calls=max_calls,
    )


@pytest.mark.anyio
async def test_default_off_never_reserves_or_sends() -> None:
    store = FakeStore()
    telegram = FakeTelegram()
    result = await _service(
        store=store,
        telegram=telegram,
        enabled=False,
        max_calls=0,
    ).execute(workspace_key="mark-personal", now=datetime.now(UTC))
    assert result == BecomeFreeResultDeliveryResult(disposition="disabled")
    assert store.reserve_calls == 0
    assert telegram.calls == 0


@pytest.mark.anyio
async def test_one_private_delivery_is_terminal_and_duplicate_safe() -> None:
    store = FakeStore()
    telegram = FakeTelegram()
    service = _service(store=store, telegram=telegram)
    first = await service.execute(
        workspace_key="mark-personal",
        now=datetime.now(UTC),
    )
    second = await service.execute(
        workspace_key="mark-personal",
        now=datetime.now(UTC),
    )
    assert first.sent is True
    assert second.sent is True
    assert second.duplicate is True
    assert telegram.calls == 1


@pytest.mark.anyio
async def test_wrong_group_fails_before_reservation() -> None:
    store = FakeStore()
    telegram = FakeTelegram()
    with pytest.raises(
        BecomeControlledIntakeError,
        match="owner_only_required",
    ):
        await _service(
            store=store,
            telegram=telegram,
            presence=FakePresence(3),
        ).execute(
            workspace_key="mark-personal",
            now=datetime.now(UTC),
        )
    assert store.reserve_calls == 0
    assert telegram.calls == 0


@pytest.mark.anyio
async def test_unknown_provider_response_never_retries() -> None:
    store = FakeStore()
    telegram = FakeTelegram(unknown=True)
    service = _service(store=store, telegram=telegram)
    first = await service.execute(
        workspace_key="mark-personal",
        now=datetime.now(UTC),
    )
    second = await service.execute(
        workspace_key="mark-personal",
        now=datetime.now(UTC),
    )
    assert first.state is BecomeFreeResultState.UNKNOWN
    assert first.reconciliation_required is True
    assert second.duplicate is True
    assert telegram.calls == 1


def test_artifact_and_delivery_receipts_are_content_free() -> None:
    text = build_free_result_text("Существующая транскрипция")
    artifact = build_artifact_receipt(
        correlation_ref=CORRELATION,
        take_receipt_digest=TAKE_DIGEST,
        transcript_digest=TRANSCRIPT_DIGEST,
        exact_result_digest=result_digest(text),
    )
    delivery = build_delivery_receipt(
        correlation_ref=CORRELATION,
        exact_result_digest=result_digest(text),
        take_receipt_digest=TAKE_DIGEST,
        destination_binding_digest=DESTINATION_DIGEST,
        state=BecomeFreeResultState.SENT,
    )
    assert "transcript" not in artifact
    assert "text" not in artifact
    assert "provider_message_id" not in delivery
    assert delivery["external_effects"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"result_digest": "4" * 64},
        {"take_receipt_digest": "5" * 64},
        {"transcript": "private"},
        {"external_effects": 1},
    ],
)
def test_mutated_artifact_receipt_fails_closed(
    mutation: dict[str, object],
) -> None:
    text = build_free_result_text("Существующая транскрипция")
    original = build_artifact_receipt(
        correlation_ref=CORRELATION,
        take_receipt_digest=TAKE_DIGEST,
        transcript_digest=TRANSCRIPT_DIGEST,
        exact_result_digest=result_digest(text),
    )
    with pytest.raises(ValueError):
        validate_receipt_replay(
            {**original, **mutation},
            expected_digest=canonical_digest(original),
        )


def test_destination_mutation_fails_closed() -> None:
    delivery = build_delivery_receipt(
        correlation_ref=CORRELATION,
        exact_result_digest="4" * 64,
        take_receipt_digest=TAKE_DIGEST,
        destination_binding_digest=DESTINATION_DIGEST,
        state=BecomeFreeResultState.SENT,
    )
    mutated = replace(
        BecomeFreeResultCandidate(
            candidate_key="candidate",
            correlation_ref=CORRELATION,
            result_digest="4" * 64,
            take_receipt_digest=TAKE_DIGEST,
            destination_binding_digest=DESTINATION_DIGEST,
            installation_ref="installation",
            target_chat_id="private-group",
        ),
        destination_binding_digest="5" * 64,
    )
    assert mutated.destination_binding_digest != (
        delivery["destination_binding_digest"]
    )
    with pytest.raises(ValueError):
        validate_receipt_replay(
            {
                **delivery,
                "destination_binding_digest": (
                    mutated.destination_binding_digest
                ),
            },
            expected_digest=canonical_digest(delivery),
            delivery=True,
        )
