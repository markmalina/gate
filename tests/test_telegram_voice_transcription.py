"""Voice bytes stay in Gate while only normalized text may reach Alpha."""

from datetime import UTC, datetime

import pytest
from app.application.billing import (
    BudgetAdmission,
    BudgetReservation,
    BudgetWindow,
    ReserveBudgetCommand,
    UsageUnit,
    admit_budget,
)
from app.application.telegram_voice_transcription import (
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    NormalizedVoice,
    ProviderTranscript,
    TelegramVoiceOutcome,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
    TranscriptionProviderError,
)

NOW = datetime(2026, 7, 17, 8, tzinfo=UTC)


class FakeDownloader:
    def __init__(self) -> None:
        self.calls: list[TelegramVoiceReference] = []

    async def download(self, voice: TelegramVoiceReference) -> DownloadedVoice:
        self.calls.append(voice)
        return DownloadedVoice(b"ogg-bytes", "voice.ogg", "audio/ogg")


class FakeNormalizer:
    def __init__(self) -> None:
        self.calls: list[DownloadedVoice] = []

    async def normalize(self, voice: DownloadedVoice) -> NormalizedVoice:
        self.calls.append(voice)
        return NormalizedVoice(b"wav-bytes", "voice.wav", "audio/wav")


class FakeBudget:
    def __init__(self, limit_microusd: int = 50_000_000) -> None:
        self.limit_microusd = limit_microusd
        self.calls: list[tuple[ReserveBudgetCommand, str]] = []
        self.deny_replays = False
        self._seen: set[str] = set()
        self.settlements: list[tuple[str, int, int, UsageUnit, str | None]] = []
        self.releases: list[str] = []
        self.unknowns: list[tuple[str, str]] = []

    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission:
        self.calls.append((command, period_key))
        if self.deny_replays and command.reservation_key in self._seen:
            return BudgetAdmission(
                allowed=False,
                reason_code="billing_reservation_replayed",
            )
        self._seen.add(command.reservation_key)
        return admit_budget(
            BudgetWindow(period_key, self.limit_microusd, 0, 0),
            command,
        )

    async def settle(
        self,
        reservation: BudgetReservation,
        *,
        actual_microusd: int,
        quantity: int,
        unit: UsageUnit,
        provider_operation_ref: str | None,
        occurred_at: datetime,
    ) -> None:
        assert occurred_at == NOW
        self.settlements.append(
            (
                reservation.reservation_key,
                actual_microusd,
                quantity,
                unit,
                provider_operation_ref,
            )
        )

    async def release(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
    ) -> None:
        assert occurred_at == NOW
        self.releases.append(reservation.reservation_key)

    async def mark_unknown(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
        error_code: str,
    ) -> None:
        assert occurred_at == NOW
        self.unknowns.append((reservation.reservation_key, error_code))


class FakeSpeechToText:
    def __init__(self) -> None:
        self.calls: list[tuple[NormalizedVoice, str | None]] = []
        self.error: TranscriptionProviderError | None = None
        self.ready = True

    async def transcribe(
        self,
        voice: NormalizedVoice,
        *,
        language: str | None,
    ) -> ProviderTranscript:
        self.calls.append((voice, language))
        if self.error is not None:
            raise self.error
        return ProviderTranscript(
            "Текст голосового сообщения",
            "request-opaque",
            billed_quantity=820,
            actual_microusd=1_100,
        )


def command(
    *,
    enabled: bool = True,
    declared_size_bytes: int | None = 100,
    requested_microusd: int = 10_000,
) -> TranscribeTelegramVoiceCommand:
    return TranscribeTelegramVoiceCommand(
        voice=TelegramVoiceReference(
            file_id="telegram-file-secret",
            file_unique_id="telegram-unique-secret",
            declared_size_bytes=declared_size_bytes,
            duration_seconds=12,
        ),
        workspace_key="mark-personal",
        connector_ref="opaque-telegram-connector",
        reservation_key="telegram:voice:update-1:message-2",
        period_key="2026-07",
        requested_microusd=requested_microusd,
        occurred_at=NOW,
        language="ru",
        enabled=enabled,
    )


def service() -> tuple[
    TelegramVoiceTranscriptionService,
    FakeDownloader,
    FakeNormalizer,
    FakeBudget,
    FakeSpeechToText,
]:
    downloader = FakeDownloader()
    normalizer = FakeNormalizer()
    budget = FakeBudget()
    speech = FakeSpeechToText()
    return (
        TelegramVoiceTranscriptionService(
            downloader=downloader,
            normalizer=normalizer,
            budget=budget,
            speech_to_text=speech,
        ),
        downloader,
        normalizer,
        budget,
        speech,
    )


@pytest.mark.anyio
async def test_disabled_voice_path_performs_no_io() -> None:
    worker, downloader, normalizer, budget, speech = service()

    result = await worker.transcribe(command(enabled=False))

    assert result.outcome is TelegramVoiceOutcome.DISABLED
    assert result.route_to_alpha_allowed is False
    assert downloader.calls == []
    assert normalizer.calls == []
    assert budget.calls == []
    assert speech.calls == []


@pytest.mark.anyio
async def test_oversize_voice_is_rejected_before_download() -> None:
    worker, downloader, _normalizer, budget, speech = service()

    result = await worker.transcribe(
        command(declared_size_bytes=TELEGRAM_DOWNLOAD_LIMIT_BYTES + 1)
    )

    assert result.reason_code == "telegram_voice_download_limit_exceeded"
    assert downloader.calls == []
    assert budget.calls == []
    assert speech.calls == []


@pytest.mark.anyio
async def test_gate_transcribes_then_exposes_only_text_to_alpha() -> None:
    worker, downloader, normalizer, budget, speech = service()

    result = await worker.transcribe(command())

    assert result.outcome is TelegramVoiceOutcome.TRANSCRIBED
    assert result.alpha_input_text == "Текст голосового сообщения"
    assert result.route_to_alpha_allowed is True
    assert result.telegram_delivery_allowed is False
    assert len(downloader.calls) == 1
    assert len(normalizer.calls) == 1
    assert speech.calls[0][0].filename == "voice.wav"
    assert speech.calls[0][1] == "ru"
    reservation, period_key = budget.calls[0]
    assert reservation.attribution.product.value == "gate"
    assert reservation.attribution.scope_kind == "telegram_connector"
    assert reservation.operation.value == "transcription"
    assert period_key == "2026-07"
    assert budget.settlements == [
        (
            "telegram:voice:update-1:message-2",
            1_100,
            820,
            UsageUnit.TOKENS,
            "request-opaque",
        )
    ]
    assert budget.releases == []
    assert budget.unknowns == []
    rendered = repr(result)
    assert "telegram-file-secret" not in rendered
    assert "opaque-telegram-connector" not in rendered


@pytest.mark.anyio
async def test_hard_budget_denial_prevents_provider_call() -> None:
    worker, _downloader, _normalizer, budget, speech = service()
    budget.limit_microusd = 9_999

    result = await worker.transcribe(command(requested_microusd=10_000))

    assert result.outcome is TelegramVoiceOutcome.REJECTED
    assert result.reason_code == "billing_hard_limit_exceeded"
    assert result.alpha_input_text is None
    assert speech.calls == []


@pytest.mark.anyio
async def test_unknown_provider_outcome_never_routes_to_alpha_or_retries() -> None:
    worker, _downloader, _normalizer, budget, speech = service()
    budget.deny_replays = True
    speech.error = TranscriptionProviderError(
        "openai_transcription_transport_unknown",
        outcome_unknown=True,
    )

    result = await worker.transcribe(command())

    assert result.outcome is TelegramVoiceOutcome.UNKNOWN
    assert result.alpha_input_text is None
    assert result.route_to_alpha_allowed is False
    assert result.telegram_delivery_allowed is False
    assert budget.unknowns == [
        (
            "telegram:voice:update-1:message-2",
            "openai_transcription_transport_unknown",
        )
    ]
    assert budget.settlements == []

    replay = await worker.transcribe(command())
    assert replay.reason_code == "billing_reservation_replayed"
    assert len(speech.calls) == 1


@pytest.mark.anyio
async def test_definite_provider_rejection_releases_reservation() -> None:
    worker, _downloader, _normalizer, budget, speech = service()
    speech.error = TranscriptionProviderError(
        "openai_transcription_rejected",
        outcome_unknown=False,
    )

    result = await worker.transcribe(command())

    assert result.outcome is TelegramVoiceOutcome.REJECTED
    assert budget.releases == ["telegram:voice:update-1:message-2"]
    assert budget.settlements == []
    assert budget.unknowns == []
