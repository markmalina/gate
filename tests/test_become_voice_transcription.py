from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.become_voice_transcription import (
    BecomeTranscriptionBudget,
    BecomeVoiceNormalizer,
    BecomeVoiceTranscriptionState,
    BecomeVoiceTranscriptionStore,
    BecomeVoiceTranscriptionWork,
    ProcessBecomeVoiceTranscriptionService,
)
from app.application.billing import (
    BillingProduct,
    BudgetAdmission,
    BudgetReservation,
    BudgetReservationState,
    UsageAttribution,
    UsageOperation,
)
from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    ProviderTranscript,
    SpeechToTextPort,
    TranscriptionProviderError,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def work(
    state: BecomeVoiceTranscriptionState,
) -> BecomeVoiceTranscriptionWork:
    return BecomeVoiceTranscriptionWork(
        attempt_id="11111111-1111-1111-1111-111111111111",
        workspace_key="workspace-test",
        correlation_ref="synthetic://correlation/voice/one",
        intake_key="become_media_one",
        state=state,
        reservation_key="become-transcription:become_media_one",
        period_key="2026-08",
        requested_microusd=1_000,
        occurred_at=NOW,
        model="gpt-4o-mini-transcribe",
        language="ru",
        raw_data=b"ogg",
        raw_media_type="audio/ogg",
        provider_call_started_at=(
            NOW if state is BecomeVoiceTranscriptionState.CALLING else None
        ),
        billed_quantity=(
            10
            if state
            is BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED
            else None
        ),
        actual_microusd=(
            20
            if state
            is BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED
            else None
        ),
    )


def reservation(
    state: BudgetReservationState = BudgetReservationState.RESERVED,
) -> BudgetReservation:
    return BudgetReservation(
        reservation_key="become-transcription:become_media_one",
        attribution=UsageAttribution(
            workspace_key="workspace-test",
            product=BillingProduct.GATE,
            scope_kind="become_media_intake",
            scope_ref="telegram_become_group",
        ),
        operation=UsageOperation.TRANSCRIPTION,
        period_key="2026-08",
        reserved_microusd=1_000,
        state=state,
        created_at=NOW,
        updated_at=NOW,
        settled_microusd=(20 if state is BudgetReservationState.SETTLED else None),
    )


class FakeStore:
    def __init__(self, item: BecomeVoiceTranscriptionWork | None) -> None:
        self.item = item
        self.events: list[str] = []

    async def claim_or_resume(self, **_: object) -> BecomeVoiceTranscriptionWork | None:
        self.events.append("claim")
        return self.item

    async def mark_blocked(self, **_: object) -> None:
        self.events.append("blocked")

    async def mark_reserved(self, **_: object) -> BecomeVoiceTranscriptionWork:
        self.events.append("reserved")
        assert self.item is not None
        self.item = replace(
            self.item,
            state=BecomeVoiceTranscriptionState.RESERVED,
        )
        return self.item

    async def begin_call(self, **_: object) -> BecomeVoiceTranscriptionWork | None:
        self.events.append("calling")
        assert self.item is not None
        self.item = replace(
            self.item,
            state=BecomeVoiceTranscriptionState.CALLING,
            provider_call_started_at=NOW,
        )
        return self.item

    async def persist_success(
        self,
        **_: object,
    ) -> BecomeVoiceTranscriptionWork:
        self.events.append("succeeded_unsettled")
        assert self.item is not None
        self.item = replace(
            self.item,
            state=BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED,
            billed_quantity=10,
            actual_microusd=20,
        )
        return self.item

    async def confirm_success(self, **_: object) -> None:
        self.events.append("confirmed")

    async def mark_terminal(
        self,
        *,
        state: BecomeVoiceTranscriptionState,
        **_: object,
    ) -> None:
        self.events.append(state.value)


class FakeBudget:
    def __init__(self, current: BudgetReservation | None = None) -> None:
        self.current = current
        self.events: list[str] = []

    async def reserve(self, command: object, *, period_key: str) -> BudgetAdmission:
        del command, period_key
        self.events.append("reserve")
        self.current = reservation()
        return BudgetAdmission(
            allowed=True,
            reason_code="billing_reserved",
            reservation=self.current,
        )

    async def get_exact(
        self,
        command: object,
        *,
        period_key: str,
    ) -> BudgetReservation | None:
        del command, period_key
        return self.current

    async def settle(self, *_: object, **__: object) -> None:
        self.events.append("settle")
        self.current = reservation(BudgetReservationState.SETTLED)

    async def release(self, *_: object, **__: object) -> None:
        self.events.append("release")

    async def mark_unknown(self, *_: object, **__: object) -> None:
        self.events.append("unknown")


class FakeNormalizer:
    def __init__(self, *, multipart: bool = False) -> None:
        self.multipart = multipart

    async def normalize(self, _: object) -> NormalizedVoice:
        extra = (
            (NormalizedVoice(b"two", "two.wav", "audio/wav"),)
            if self.multipart
            else ()
        )
        return NormalizedVoice(
            b"one",
            "one.wav",
            "audio/wav",
            additional_parts=extra,
        )


class FakeSpeech:
    ready = True

    def __init__(self, error: TranscriptionProviderError | None = None) -> None:
        self.error = error
        self.calls = 0

    async def transcribe(self, *_: object, **__: object) -> ProviderTranscript:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ProviderTranscript(
            "подтвержденный текст",
            "request-safe",
            billed_quantity=10,
            actual_microusd=20,
        )


def service(
    store: FakeStore,
    budget: FakeBudget,
    speech: FakeSpeech,
    *,
    normalizer: FakeNormalizer | None = None,
    enabled: bool = True,
) -> ProcessBecomeVoiceTranscriptionService:
    return ProcessBecomeVoiceTranscriptionService(
        store=cast(BecomeVoiceTranscriptionStore, store),
        normalizer=cast(
            BecomeVoiceNormalizer,
            normalizer or FakeNormalizer(),
        ),
        budget=cast(BecomeTranscriptionBudget, budget),
        speech_to_text=cast(SpeechToTextPort, speech),
        workspace_key="workspace-test",
        model="gpt-4o-mini-transcribe",
        language="ru",
        reserve_microusd_per_second=100,
        enabled=enabled,
        data_gate_approved=enabled,
        max_calls=1 if enabled else 0,
    )


@pytest.mark.anyio
async def test_defaults_disable_without_store_or_provider_io() -> None:
    store = FakeStore(work(BecomeVoiceTranscriptionState.RESERVING))
    budget = FakeBudget()
    speech = FakeSpeech()

    result = await service(
        store,
        budget,
        speech,
        enabled=False,
    ).run_once(now=NOW)

    assert result.disposition == "disabled"
    assert store.events == []
    assert budget.events == []
    assert speech.calls == 0


@pytest.mark.anyio
async def test_calling_is_durable_before_single_provider_call() -> None:
    store = FakeStore(work(BecomeVoiceTranscriptionState.RESERVING))
    budget = FakeBudget()
    speech = FakeSpeech()

    result = await service(store, budget, speech).run_once(now=NOW)

    assert result.transcript_confirmed is True
    assert speech.calls == 1
    assert store.events == [
        "claim",
        "reserved",
        "calling",
        "succeeded_unsettled",
        "confirmed",
    ]
    assert budget.events == ["reserve", "settle"]


@pytest.mark.anyio
async def test_multipart_is_blocked_before_reservation_and_calling() -> None:
    store = FakeStore(work(BecomeVoiceTranscriptionState.RESERVING))
    budget = FakeBudget()
    speech = FakeSpeech()

    result = await service(
        store,
        budget,
        speech,
        normalizer=FakeNormalizer(multipart=True),
    ).run_once(now=NOW)

    assert result.disposition == "blocked"
    assert store.events == ["claim", "blocked"]
    assert budget.events == []
    assert speech.calls == 0


@pytest.mark.anyio
async def test_unknown_provider_outcome_is_never_retried() -> None:
    store = FakeStore(work(BecomeVoiceTranscriptionState.RESERVED))
    budget = FakeBudget(reservation())
    speech = FakeSpeech(
        TranscriptionProviderError(
            "openai_transcription_transport_unknown",
            outcome_unknown=True,
        )
    )

    result = await service(store, budget, speech).run_once(now=NOW)

    assert result.reconciliation_required is True
    assert result.provider_call_boundary_entered is True
    assert speech.calls == 1
    assert store.events == ["claim", "calling", "unknown"]
    assert budget.events == ["unknown"]


@pytest.mark.anyio
async def test_succeeded_unsettled_recovers_without_provider_call() -> None:
    store = FakeStore(
        work(BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED)
    )
    budget = FakeBudget(reservation())
    speech = FakeSpeech()

    result = await service(store, budget, speech).run_once(now=NOW)

    assert result.transcript_confirmed is True
    assert speech.calls == 0
    assert budget.events == ["settle"]
    assert store.events == ["claim", "confirmed"]
