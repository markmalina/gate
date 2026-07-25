"""Durably archive one Telegram Business voice without exposing provider identifiers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.telegram_voice_transcription import (
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    TelegramVoiceDownloadPort,
    TelegramVoiceError,
)
from app.application.telegram_voice_update import extract_telegram_voice

JsonObject = dict[str, Any]


class TelegramVoiceArchiveDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class TelegramVoiceTranscriptStatus(StrEnum):
    DISABLED = "disabled"
    REJECTED = "rejected"
    TRANSCRIBED = "transcribed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ArchiveTelegramVoiceCommand:
    receipt_id: str = field(repr=False)
    account_id: str = field(repr=False)
    project_id: str = field(repr=False)
    direction: TelegramVoiceArchiveDirection
    update: JsonObject = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramVoiceArchiveRecord:
    receipt_id: str = field(repr=False)
    account_id: str = field(repr=False)
    project_id: str = field(repr=False)
    direction: TelegramVoiceArchiveDirection
    telegram_update_id: int
    telegram_message_id: int
    chat_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    duration_seconds: int
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    data: bytes = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramVoiceArchiveResult:
    archive_id: str
    duplicate: bool
    size_bytes: int
    media_type: str
    transcription_status: TelegramVoiceTranscriptStatus | None = None
    transcript_text: str | None = field(default=None, repr=False)
    transcription_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PersistTelegramVoiceTranscriptCommand:
    archive_id: str
    status: TelegramVoiceTranscriptStatus
    transcribed_at: datetime
    text: str | None = field(default=None, repr=False)
    language: str = "ru"
    model: str = "gpt-4o-mini-transcribe"
    reason_code: str = "telegram_voice_transcribed"

    def __post_init__(self) -> None:
        if not self.archive_id or len(self.archive_id) > 128:
            raise TelegramVoiceError("telegram_voice_archive_id_invalid")
        if self.status is TelegramVoiceTranscriptStatus.TRANSCRIBED:
            if (
                self.text is None
                or not self.text.strip()
                or self.text != self.text.strip()
                or len(self.text) > 100_000
            ):
                raise TelegramVoiceError("telegram_voice_transcript_invalid")
        elif self.text is not None:
            raise TelegramVoiceError("telegram_voice_transcript_status_invalid")
        for value, code, maximum in (
            (self.language, "telegram_voice_transcript_language_invalid", 32),
            (self.model, "telegram_voice_transcript_model_invalid", 64),
            (self.reason_code, "telegram_voice_transcript_reason_invalid", 128),
        ):
            if not value or value != value.strip() or len(value) > maximum:
                raise TelegramVoiceError(code)
        if (
            self.transcribed_at.tzinfo is None
            or self.transcribed_at.utcoffset() is None
        ):
            raise TelegramVoiceError("telegram_voice_transcript_time_invalid")


@dataclass(frozen=True, slots=True)
class TelegramVoiceTranscriptPersistenceResult:
    archive_id: str
    duplicate: bool
    status: TelegramVoiceTranscriptStatus


class TelegramVoiceArchiveStore(Protocol):
    async def persist(
        self,
        record: TelegramVoiceArchiveRecord,
    ) -> TelegramVoiceArchiveResult: ...

    async def persist_transcript(
        self,
        command: PersistTelegramVoiceTranscriptCommand,
    ) -> TelegramVoiceTranscriptPersistenceResult: ...


class TelegramVoiceArchiver(Protocol):
    async def archive(
        self,
        command: ArchiveTelegramVoiceCommand,
    ) -> TelegramVoiceArchiveResult: ...

    async def persist_transcript(
        self,
        command: PersistTelegramVoiceTranscriptCommand,
    ) -> TelegramVoiceTranscriptPersistenceResult: ...


class ArchiveTelegramVoiceService:
    """Download and persist one exact durable Business voice receipt."""

    def __init__(
        self,
        *,
        downloader: TelegramVoiceDownloadPort,
        store: TelegramVoiceArchiveStore,
    ) -> None:
        self._downloader = downloader
        self._store = store

    async def archive(
        self,
        command: ArchiveTelegramVoiceCommand,
    ) -> TelegramVoiceArchiveResult:
        envelope = extract_telegram_voice(command.update)
        if envelope is None or envelope.message_key != "business_message":
            raise TelegramVoiceError("telegram_voice_archive_not_applicable")
        message = command.update.get(envelope.message_key)
        if not isinstance(message, dict):
            raise TelegramVoiceError("telegram_voice_archive_message_invalid")
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        business_connection_id = message.get("business_connection_id")
        if chat_id is None:
            raise TelegramVoiceError("telegram_voice_archive_chat_invalid")
        if (
            not isinstance(business_connection_id, str)
            or not business_connection_id.strip()
        ):
            raise TelegramVoiceError("telegram_voice_archive_connection_invalid")
        if command.direction not in {
            TelegramVoiceArchiveDirection.INBOUND,
            TelegramVoiceArchiveDirection.OUTBOUND,
        }:
            raise TelegramVoiceError("telegram_voice_archive_direction_invalid")

        downloaded = await self._downloader.download(envelope.voice)
        _validate_download(downloaded)
        media_type = (
            downloaded.media_type
            if downloaded.media_type.startswith("audio/")
            else "audio/ogg"
        )
        record = TelegramVoiceArchiveRecord(
            receipt_id=command.receipt_id,
            account_id=command.account_id,
            project_id=command.project_id,
            direction=command.direction,
            telegram_update_id=envelope.update_id,
            telegram_message_id=envelope.message_id,
            chat_id=str(chat_id),
            business_connection_id=business_connection_id,
            duration_seconds=envelope.voice.duration_seconds,
            filename=downloaded.filename,
            media_type=media_type,
            size_bytes=len(downloaded.data),
            sha256=hashlib.sha256(downloaded.data).hexdigest(),
            data=downloaded.data,
            received_at=command.received_at,
        )
        return await self._store.persist(record)

    async def persist_transcript(
        self,
        command: PersistTelegramVoiceTranscriptCommand,
    ) -> TelegramVoiceTranscriptPersistenceResult:
        return await self._store.persist_transcript(command)


def _validate_download(downloaded: DownloadedVoice) -> None:
    if not downloaded.data:
        raise TelegramVoiceError("telegram_voice_archive_empty")
    if len(downloaded.data) > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
        raise TelegramVoiceError("telegram_voice_archive_limit_exceeded")
