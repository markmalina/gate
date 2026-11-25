"""Compose the Telegram worker's optional voice services."""

import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.telegram_voice_archive import ArchiveTelegramVoiceService
from app.application.telegram_voice_orchestration import ProcessTelegramVoiceUpdateService
from app.application.telegram_voice_transcription import TelegramVoiceTranscriptionService
from app.core.config import Settings
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)
from app.infrastructure.telegram_voice_archive import SessionFactoryTelegramVoiceArchiveStore


def build_telegram_voice_services(
    *,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
) -> tuple[ArchiveTelegramVoiceService | None, ProcessTelegramVoiceUpdateService | None]:
    if not (
        settings.gate_telegram_voice_archive_enabled
        or settings.gate_telegram_voice_transcription_enabled
    ):
        return None, None

    downloader = TelegramVoiceDownloader(
        config=TelegramVoiceDownloadConfig(
            bot_token=settings.telegram_bot_token,
            api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
        ),
        http=http_client,
    )
    archive = None
    if settings.gate_telegram_voice_archive_enabled:
        archive = ArchiveTelegramVoiceService(
            downloader=downloader,
            store=SessionFactoryTelegramVoiceArchiveStore(sessions),
        )

    updates = None
    if settings.gate_telegram_voice_transcription_enabled:
        api_key = settings.openai_api_key.get_secret_value()
        updates = ProcessTelegramVoiceUpdateService(
            TelegramVoiceTranscriptionService(
                downloader=downloader,
                normalizer=TelegramVoiceNormalizer(),
                budget=SqlBillingBudgetService(sessions),
                speech_to_text=OpenAITranscriptionClient(
                    config=OpenAITranscriptionConfig(
                        api_key=api_key,
                        enabled=bool(api_key),
                        model=settings.gate_telegram_voice_transcription_model,
                        base_url=settings.openai_api_base_url,
                    ),
                    http=http_client,
                ),
            )
        )
    return archive, updates
