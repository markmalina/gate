"""Voice orchestration opens downstream routing only for text-safe outcomes."""

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
from app.application.telegram_voice_orchestration import (
    ProcessTelegramVoiceUpdateCommand,
    ProcessTelegramVoiceUpdateService,
    TelegramVoiceUpdateDisposition,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    NormalizedVoice,
    ProviderTranscript,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


class Downloader:
    async def download(self, _: TelegramVoiceReference) -> DownloadedVoice:
        return DownloadedVoice(b"ogg", "voice.ogg", "audio/ogg")


class Normalizer:
    async def normalize(self, _: DownloadedVoice) -> NormalizedVoice:
        return NormalizedVoice(b"wav", "voice.wav", "audio/wav")


class Budget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    async def reserve(
        self,
        command: ReserveBudgetCommand,
        *,
        period_key: str,
    ) -> BudgetAdmission:
        if not self.allowed:
            return BudgetAdmission(False, "billing_hard_limit_exceeded")
        return admit_budget(BudgetWindow(period_key, 50_000_000, 0, 0), command)

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
        assert reservation.reservation_key
        assert actual_microusd == 0
        assert quantity == 0
        assert unit is UsageUnit.TOKENS
        assert provider_operation_ref is None
        assert occurred_at == NOW

    async def release(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
    ) -> None:
        raise AssertionError((reservation, occurred_at))

    async def mark_unknown(
        self,
        reservation: BudgetReservation,
        *,
        occurred_at: datetime,
        error_code: str,
    ) -> None:
        raise AssertionError((reservation, occurred_at, error_code))


class Speech:
    ready = True

    async def transcribe(
        self,
        _: NormalizedVoice,
        *,
        language: str | None,
    ) -> ProviderTranscript:
        assert language == "ru"
        return ProviderTranscript("Распознанный текст")


def voice_update() -> dict[str, object]:
    return {
        "update_id": 10,
        "message": {
            "message_id": 20,
            "chat": {"id": -30, "type": "supergroup"},
            "voice": {
                "file_id": "file-id",
                "file_unique_id": "file-unique-id",
                "file_size": 100,
                "duration": 4,
            },
        },
    }


def command(
    update: dict[str, object],
    *,
    enabled: bool = True,
) -> ProcessTelegramVoiceUpdateCommand:
    return ProcessTelegramVoiceUpdateCommand(
        update=update,
        workspace_key="mark-personal",
        connector_ref="opaque-connector",
        period_key="2026-07",
        requested_microusd=10_000,
        occurred_at=NOW,
        language="ru",
        enabled=enabled,
    )


def service(*, budget_allowed: bool = True) -> ProcessTelegramVoiceUpdateService:
    transcription = TelegramVoiceTranscriptionService(
        downloader=Downloader(),
        normalizer=Normalizer(),
        budget=Budget(allowed=budget_allowed),
        speech_to_text=Speech(),
    )
    return ProcessTelegramVoiceUpdateService(transcription)


@pytest.mark.anyio
async def test_plain_text_update_continues_without_transcription() -> None:
    original = {
        "update_id": 1,
        "message": {"message_id": 2, "text": "Обычный текст"},
    }

    result = await service().execute(command(original))

    assert result.disposition is TelegramVoiceUpdateDisposition.NOT_APPLICABLE
    assert result.downstream_allowed is True
    assert result.downstream_update is original


@pytest.mark.anyio
async def test_successful_voice_becomes_text_only_downstream_update() -> None:
    result = await service().execute(command(voice_update()))

    assert result.disposition is TelegramVoiceUpdateDisposition.PROJECTED
    assert result.downstream_allowed is True
    assert result.downstream_update is not None
    message = result.downstream_update["message"]
    assert isinstance(message, dict)
    assert message["text"] == "Распознанный текст"
    assert "voice" not in message


@pytest.mark.anyio
async def test_disabled_voice_never_falls_through_as_raw_update() -> None:
    result = await service().execute(command(voice_update(), enabled=False))

    assert result.disposition is TelegramVoiceUpdateDisposition.DISABLED
    assert result.downstream_allowed is False
    assert result.downstream_update is None


@pytest.mark.anyio
async def test_budget_denial_never_reaches_downstream_alpha_path() -> None:
    result = await service(budget_allowed=False).execute(command(voice_update()))

    assert result.disposition is TelegramVoiceUpdateDisposition.BLOCKED
    assert result.reason_code == "billing_hard_limit_exceeded"
    assert result.downstream_allowed is False
