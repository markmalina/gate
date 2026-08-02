"""One-call BECOME-only voice transcription orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.application.billing import (
    BillingProduct,
    BudgetAdmission,
    BudgetReservation,
    BudgetReservationState,
    ReserveBudgetCommand,
    UsageAttribution,
    UsageOperation,
    UsageUnit,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    NormalizedVoice,
    ProviderTranscript,
    SpeechToTextPort,
    TranscriptionProviderError,
)

BECOME_TRANSCRIPTION_SCHEMA = "gate.become-voice-transcription@1"
BECOME_TRANSCRIPTION_GRANT_KEY = "become-investor-demo-transcription-v1"
BECOME_TRANSCRIPTION_SCOPE_KIND = "become_media_intake"
BECOME_TRANSCRIPTION_SCOPE_REF = "telegram_become_group"


class BecomeVoiceTranscriptionState(StrEnum):
    RESERVING = "reserving"
    RESERVED = "reserved"
    CALLING = "calling"
    SUCCEEDED_UNSETTLED = "succeeded_unsettled"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class BecomeVoiceTranscriptionWork:
    attempt_id: str
    workspace_key: str
    correlation_ref: str
    intake_key: str
    state: BecomeVoiceTranscriptionState
    reservation_key: str
    period_key: str
    requested_microusd: int
    occurred_at: datetime
    model: str
    language: str
    raw_data: bytes = field(repr=False)
    raw_media_type: str
    provider_call_started_at: datetime | None = None
    provider_operation_ref: str | None = field(default=None, repr=False)
    billed_quantity: int | None = None
    actual_microusd: int | None = None


@dataclass(frozen=True, slots=True)
class BecomeVoiceTranscriptionRun:
    disposition: str
    provider_call_boundary_entered: bool = False
    transcript_confirmed: bool = False
    reconciliation_required: bool = False


class BecomeVoiceTranscriptionStore(Protocol):
    async def claim_or_resume(
        self,
        *,
        workspace_key: str,
        now: datetime,
        model: str,
        language: str,
        reserve_microusd_per_second: int,
        stale_after: timedelta,
    ) -> BecomeVoiceTranscriptionWork | None: ...

    async def mark_blocked(
        self,
        *,
        attempt_id: str,
        reason_code: str,
        now: datetime,
    ) -> None: ...

    async def mark_reserved(
        self,
        *,
        attempt_id: str,
        now: datetime,
    ) -> BecomeVoiceTranscriptionWork: ...

    async def begin_call(
        self,
        *,
        attempt_id: str,
        now: datetime,
    ) -> BecomeVoiceTranscriptionWork | None: ...

    async def persist_success(
        self,
        *,
        attempt_id: str,
        transcript: ProviderTranscript,
        confirmed_at: datetime,
    ) -> BecomeVoiceTranscriptionWork: ...

    async def mark_terminal(
        self,
        *,
        attempt_id: str,
        state: BecomeVoiceTranscriptionState,
        reason_code: str | None,
        now: datetime,
    ) -> None: ...


class BecomeTranscriptionBudget(Protocol):
    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission: ...

    async def get_exact(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetReservation | None: ...

    async def settle(
        self,
        reservation: BudgetReservation,
        *,
        actual_microusd: int,
        quantity: int,
        unit: UsageUnit,
        provider_operation_ref: str | None,
        occurred_at: datetime,
    ) -> None: ...

    async def release(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
    ) -> None: ...

    async def mark_unknown(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
        error_code: str,
    ) -> None: ...


class BecomeVoiceNormalizer(Protocol):
    async def normalize(self, voice: DownloadedVoice) -> NormalizedVoice: ...


class ProcessBecomeVoiceTranscriptionService:
    """Enter the provider boundary once; never route to Alpha or delivery."""

    def __init__(
        self,
        *,
        store: BecomeVoiceTranscriptionStore,
        normalizer: BecomeVoiceNormalizer,
        budget: BecomeTranscriptionBudget,
        speech_to_text: SpeechToTextPort,
        workspace_key: str,
        model: str,
        language: str,
        reserve_microusd_per_second: int,
        enabled: bool,
        data_gate_approved: bool,
        max_calls: int,
        stale_seconds: int = 300,
    ) -> None:
        if max_calls not in {0, 1}:
            raise ValueError("become_transcription_call_limit_invalid")
        if reserve_microusd_per_second < 100:
            raise ValueError("become_transcription_reserve_rate_invalid")
        self._store = store
        self._normalizer = normalizer
        self._budget = budget
        self._speech = speech_to_text
        self._workspace_key = workspace_key
        self._model = model
        self._language = language
        self._rate = reserve_microusd_per_second
        self._enabled = enabled
        self._data_gate_approved = data_gate_approved
        self._max_calls = max_calls
        self._stale_after = timedelta(seconds=stale_seconds)

    async def run_once(self, *, now: datetime) -> BecomeVoiceTranscriptionRun:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("become_transcription_time_invalid")
        if (
            not self._enabled
            or not self._data_gate_approved
            or self._max_calls != 1
            or not self._speech.ready
        ):
            return BecomeVoiceTranscriptionRun("disabled")

        work = await self._store.claim_or_resume(
            workspace_key=self._workspace_key,
            now=now,
            model=self._model,
            language=self._language,
            reserve_microusd_per_second=self._rate,
            stale_after=self._stale_after,
        )
        if work is None:
            return BecomeVoiceTranscriptionRun("idle")

        command = _budget_command(work)
        reservation = await self._budget.get_exact(
            command,
            period_key=work.period_key,
        )

        if work.state is BecomeVoiceTranscriptionState.CALLING:
            if reservation is not None:
                if reservation.state is BudgetReservationState.RESERVED:
                    await self._budget.mark_unknown(
                        reservation,
                        occurred_at=now,
                        error_code="become_transcription_stale_call_unknown",
                    )
                elif reservation.state is BudgetReservationState.RELEASED:
                    await self._store.mark_terminal(
                        attempt_id=work.attempt_id,
                        state=BecomeVoiceTranscriptionState.REJECTED,
                        reason_code="become_transcription_rejected",
                        now=now,
                    )
                    return BecomeVoiceTranscriptionRun("rejected")
            await self._store.mark_terminal(
                attempt_id=work.attempt_id,
                state=BecomeVoiceTranscriptionState.UNKNOWN,
                reason_code="become_transcription_stale_call_unknown",
                now=now,
            )
            return BecomeVoiceTranscriptionRun(
                "unknown",
                reconciliation_required=True,
            )

        if work.state is BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED:
            if (
                work.billed_quantity is None
                or work.actual_microusd is None
                or reservation is None
            ):
                return BecomeVoiceTranscriptionRun(
                    "settlement_missing",
                    reconciliation_required=True,
                )
            if reservation.state is BudgetReservationState.RESERVED:
                await self._budget.settle(
                    reservation,
                    actual_microusd=work.actual_microusd,
                    quantity=work.billed_quantity,
                    unit=UsageUnit.TOKENS,
                    provider_operation_ref=work.provider_operation_ref,
                    occurred_at=now,
                )
            elif reservation.state is not BudgetReservationState.SETTLED:
                await self._store.mark_terminal(
                    attempt_id=work.attempt_id,
                    state=BecomeVoiceTranscriptionState.UNKNOWN,
                    reason_code="become_transcription_settlement_unknown",
                    now=now,
                )
                return BecomeVoiceTranscriptionRun(
                    "settlement_unknown",
                    reconciliation_required=True,
                )
            await self._store.mark_terminal(
                attempt_id=work.attempt_id,
                state=BecomeVoiceTranscriptionState.CONFIRMED,
                reason_code=None,
                now=now,
            )
            return BecomeVoiceTranscriptionRun(
                "confirmed",
                transcript_confirmed=True,
            )

        if work.state is BecomeVoiceTranscriptionState.RESERVING:
            try:
                normalized = await self._normalizer.normalize(
                    DownloadedVoice(
                        data=work.raw_data,
                        filename=_filename(work.raw_media_type),
                        media_type=work.raw_media_type,
                    )
                )
            except Exception:
                await self._store.mark_blocked(
                    attempt_id=work.attempt_id,
                    reason_code="become_transcription_normalization_failed",
                    now=now,
                )
                return BecomeVoiceTranscriptionRun("blocked")
            if normalized.additional_parts:
                await self._store.mark_blocked(
                    attempt_id=work.attempt_id,
                    reason_code="become_transcription_single_call_required",
                    now=now,
                )
                return BecomeVoiceTranscriptionRun("blocked")
            if reservation is None:
                admission = await self._budget.reserve(
                    command,
                    period_key=work.period_key,
                )
                if not admission.allowed:
                    await self._store.mark_blocked(
                        attempt_id=work.attempt_id,
                        reason_code=admission.reason_code,
                        now=now,
                    )
                    return BecomeVoiceTranscriptionRun("blocked")
            elif reservation.state is not BudgetReservationState.RESERVED:
                await self._store.mark_terminal(
                    attempt_id=work.attempt_id,
                    state=BecomeVoiceTranscriptionState.UNKNOWN,
                    reason_code="become_transcription_budget_not_reserved",
                    now=now,
                )
                return BecomeVoiceTranscriptionRun(
                    "budget_unknown",
                    reconciliation_required=True,
                )
            work = await self._store.mark_reserved(
                attempt_id=work.attempt_id,
                now=now,
            )
        else:
            normalized = await self._normalizer.normalize(
                DownloadedVoice(
                    data=work.raw_data,
                    filename=_filename(work.raw_media_type),
                    media_type=work.raw_media_type,
                )
            )
            if normalized.additional_parts:
                await self._store.mark_blocked(
                    attempt_id=work.attempt_id,
                    reason_code="become_transcription_single_call_required",
                    now=now,
                )
                return BecomeVoiceTranscriptionRun("blocked")

        reservation = await self._budget.get_exact(
            _budget_command(work),
            period_key=work.period_key,
        )
        if reservation is None or reservation.state is not BudgetReservationState.RESERVED:
            await self._store.mark_terminal(
                attempt_id=work.attempt_id,
                state=BecomeVoiceTranscriptionState.UNKNOWN,
                reason_code="become_transcription_reservation_missing",
                now=now,
            )
            return BecomeVoiceTranscriptionRun(
                "reservation_unknown",
                reconciliation_required=True,
            )

        calling = await self._store.begin_call(
            attempt_id=work.attempt_id,
            now=now,
        )
        if calling is None:
            return BecomeVoiceTranscriptionRun(
                "call_not_claimed",
                reconciliation_required=True,
            )

        try:
            transcript = await self._speech.transcribe(
                normalized,
                language=calling.language,
            )
        except TranscriptionProviderError as exc:
            if exc.outcome_unknown:
                await self._budget.mark_unknown(
                    reservation,
                    occurred_at=now,
                    error_code=exc.code,
                )
                terminal = BecomeVoiceTranscriptionState.UNKNOWN
            else:
                await self._budget.release(reservation, occurred_at=now)
                terminal = BecomeVoiceTranscriptionState.REJECTED
            await self._store.mark_terminal(
                attempt_id=calling.attempt_id,
                state=terminal,
                reason_code=exc.code,
                now=now,
            )
            return BecomeVoiceTranscriptionRun(
                terminal.value,
                provider_call_boundary_entered=True,
                reconciliation_required=(
                    terminal is BecomeVoiceTranscriptionState.UNKNOWN
                ),
            )

        work = await self._store.persist_success(
            attempt_id=calling.attempt_id,
            transcript=transcript,
            confirmed_at=now,
        )
        await self._budget.settle(
            reservation,
            actual_microusd=transcript.actual_microusd,
            quantity=transcript.billed_quantity,
            unit=transcript.billed_unit,
            provider_operation_ref=transcript.provider_operation_ref,
            occurred_at=now,
        )
        await self._store.mark_terminal(
            attempt_id=work.attempt_id,
            state=BecomeVoiceTranscriptionState.CONFIRMED,
            reason_code=None,
            now=now,
        )
        return BecomeVoiceTranscriptionRun(
            "confirmed",
            provider_call_boundary_entered=True,
            transcript_confirmed=True,
        )


def _budget_command(work: BecomeVoiceTranscriptionWork) -> ReserveBudgetCommand:
    return ReserveBudgetCommand(
        reservation_key=work.reservation_key,
        attribution=UsageAttribution(
            workspace_key=work.workspace_key,
            product=BillingProduct.GATE,
            scope_kind=BECOME_TRANSCRIPTION_SCOPE_KIND,
            scope_ref=BECOME_TRANSCRIPTION_SCOPE_REF,
        ),
        operation=UsageOperation.TRANSCRIPTION,
        requested_microusd=work.requested_microusd,
        occurred_at=work.occurred_at,
    )


def _filename(media_type: str) -> str:
    if media_type in {"audio/ogg", "audio/opus"}:
        return "become-voice.ogg"
    if media_type == "audio/mpeg":
        return "become-voice.mp3"
    if media_type == "audio/wav":
        return "become-voice.wav"
    return "become-voice.ogg"
