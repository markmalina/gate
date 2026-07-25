"""Unit coverage for durable Telegram Business voice archiving."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.application.telegram_voice_archive import (
    ArchiveTelegramVoiceCommand,
    ArchiveTelegramVoiceService,
    TelegramVoiceArchiveDirection,
    TelegramVoiceArchiveRecord,
    TelegramVoiceArchiveResult,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceReference,
)


class FakeDownloader:
    def __init__(self) -> None:
        self.references: list[TelegramVoiceReference] = []

    async def download(self, voice: TelegramVoiceReference) -> DownloadedVoice:
        self.references.append(voice)
        return DownloadedVoice(
            data=b"original-ogg-opus",
            filename="voice.oga",
            media_type="audio/ogg",
        )


class RecordingStore:
    def __init__(self) -> None:
        self.records: list[TelegramVoiceArchiveRecord] = []

    async def persist(
        self,
        record: TelegramVoiceArchiveRecord,
    ) -> TelegramVoiceArchiveResult:
        self.records.append(record)
        return TelegramVoiceArchiveResult(
            archive_id="archive-1",
            duplicate=False,
            size_bytes=record.size_bytes,
            media_type=record.media_type,
        )


def test_business_voice_is_downloaded_and_persisted_as_original_audio() -> None:
    downloader = FakeDownloader()
    store = RecordingStore()
    service = ArchiveTelegramVoiceService(
        downloader=downloader,
        store=store,
    )
    received_at = datetime(2026, 7, 25, 6, 30, tzinfo=UTC)

    result = asyncio.run(
        service.archive(
            ArchiveTelegramVoiceCommand(
                receipt_id="11111111-1111-1111-1111-111111111111",
                account_id="22222222-2222-2222-2222-222222222222",
                project_id="33333333-3333-3333-3333-333333333333",
                direction=TelegramVoiceArchiveDirection.INBOUND,
                update={
                    "update_id": 42,
                    "business_message": {
                        "message_id": 7,
                        "business_connection_id": "opaque-connection",
                        "chat": {"id": 99, "type": "private"},
                        "voice": {
                            "file_id": "opaque-file",
                            "file_unique_id": "opaque-unique",
                            "file_size": 1024,
                            "duration": 4,
                        },
                    },
                },
                received_at=received_at,
            )
        )
    )

    assert result.duplicate is False
    assert len(downloader.references) == 1
    assert len(store.records) == 1
    record = store.records[0]
    assert record.telegram_update_id == 42
    assert record.telegram_message_id == 7
    assert record.direction is TelegramVoiceArchiveDirection.INBOUND
    assert record.duration_seconds == 4
    assert record.data == b"original-ogg-opus"
    assert record.size_bytes == len(record.data)
    assert record.media_type == "audio/ogg"
    assert len(record.sha256) == 64
    assert record.received_at == received_at
