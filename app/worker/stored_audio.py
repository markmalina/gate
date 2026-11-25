"""Shared operations for transcription of already archived voice audio."""

from datetime import UTC, datetime

from app.application.audio_summary import AUDIO_SUMMARY_MODEL
from app.application.telegram_voice_transcription import DownloadedVoice, TelegramVoiceReference
from app.models.domain import PhoneCallRecording, TelegramVoiceArchive

# Stored audio shares the existing hard-limited Telegram voice entitlement.
STORED_VOICE_BUDGET_SCOPE_KIND = "telegram_connector"
STORED_VOICE_BUDGET_CONNECTOR_REF = "telegram_business"


class StoredVoiceDownloader:
    def __init__(
        self,
        recording: PhoneCallRecording | TelegramVoiceArchive,
    ) -> None:
        self._recording = recording

    async def download(self, _reference: TelegramVoiceReference) -> DownloadedVoice:
        return DownloadedVoice(
            data=self._recording.data,
            filename=self._recording.filename,
            media_type=self._recording.media_type,
        )


def store_no_speech_summary(archive: PhoneCallRecording | TelegramVoiceArchive) -> None:
    archive.summary_status = "summarized"
    archive.summary_text = "Распознавание завершено: речь не распознана."
    archive.summary_model = AUDIO_SUMMARY_MODEL
    archive.summary_reason_code = "audio_no_speech"
    archive.summarized_at = datetime.now(UTC)
