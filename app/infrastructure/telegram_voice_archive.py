"""PostgreSQL persistence for bounded Telegram Business voice audio."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.telegram_voice_archive import (
    ArchiveTelegramVoiceCommand,
    PersistTelegramVoiceTranscriptCommand,
    TelegramVoiceArchiveRecord,
    TelegramVoiceArchiveResult,
    TelegramVoiceArchiveStore,
    TelegramVoiceTranscriptPersistenceResult,
    TelegramVoiceTranscriptStatus,
)
from app.application.telegram_voice_transcription import DownloadedVoice, TelegramVoiceError
from app.application.telegram_voice_update import extract_telegram_voice
from app.models.domain import TelegramVoiceArchive


class SessionFactoryTelegramVoiceArchiveStore(TelegramVoiceArchiveStore):
    """Persist one voice idempotently in a short independent transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_existing(
        self, command: ArchiveTelegramVoiceCommand,
    ) -> TelegramVoiceArchiveResult | None:
        try:
            receipt_id = uuid.UUID(command.receipt_id)
            account_id = uuid.UUID(command.account_id)
            project_id = uuid.UUID(command.project_id)
        except ValueError as exc:
            raise TelegramVoiceError("telegram_voice_archive_scope_invalid") from exc
        async with self._sessions() as session:
            archive = await session.scalar(
                select(TelegramVoiceArchive).where(TelegramVoiceArchive.receipt_id == receipt_id)
            )
            if archive is None:
                return None
            envelope = extract_telegram_voice(command.update)
            if (
                envelope is None
                or archive.account_id != account_id
                or archive.project_id != project_id
                or archive.direction != command.direction.value
                or archive.telegram_update_id != envelope.update_id
                or archive.telegram_message_id != envelope.message_id
            ):
                raise TelegramVoiceError("telegram_voice_archive_replay_mismatch")
            message = command.update[envelope.message_key]
            if (
                archive.chat_id != str(message["chat"]["id"])
                or archive.business_connection_id != message["business_connection_id"]
                or archive.duration_seconds != envelope.voice.duration_seconds
            ):
                raise TelegramVoiceError("telegram_voice_archive_replay_mismatch")
            return _existing_result(archive)

    async def persist(
        self,
        record: TelegramVoiceArchiveRecord,
    ) -> TelegramVoiceArchiveResult:
        try:
            receipt_id = uuid.UUID(record.receipt_id)
            account_id = uuid.UUID(record.account_id)
            project_id = uuid.UUID(record.project_id)
        except ValueError as exc:
            raise TelegramVoiceError("telegram_voice_archive_scope_invalid") from exc

        archive_id = uuid.uuid4()
        values = {
            "id": archive_id,
            "receipt_id": receipt_id,
            "account_id": account_id,
            "project_id": project_id,
            "direction": record.direction.value,
            "telegram_update_id": record.telegram_update_id,
            "telegram_message_id": record.telegram_message_id,
            "chat_id": record.chat_id,
            "business_connection_id": record.business_connection_id,
            "duration_seconds": record.duration_seconds,
            "filename": record.filename,
            "media_type": record.media_type,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "data": record.data,
            "received_at": record.received_at,
            # The Telegram ingress transaction creates the receipt and its outbox together.
            "transcription_queued_at": record.received_at,
        }
        async with self._sessions.begin() as session:
            inserted_id = await session.scalar(
                insert(TelegramVoiceArchive)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(TelegramVoiceArchive.id)
            )
            if inserted_id is not None:
                return TelegramVoiceArchiveResult(
                    archive_id=str(inserted_id),
                    duplicate=False,
                    size_bytes=record.size_bytes,
                    media_type=record.media_type,
                    archived_audio=DownloadedVoice(
                        data=record.data, filename=record.filename, media_type=record.media_type,
                    ),
                )

            existing = (
                await session.execute(
                    select(TelegramVoiceArchive).where(
                        or_(
                            TelegramVoiceArchive.receipt_id == receipt_id,
                            (
                                (TelegramVoiceArchive.account_id == account_id)
                                & (
                                    TelegramVoiceArchive.business_connection_id
                                    == record.business_connection_id
                                )
                                & (TelegramVoiceArchive.chat_id == record.chat_id)
                                & (
                                    TelegramVoiceArchive.telegram_message_id
                                    == record.telegram_message_id
                                )
                            ),
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise TelegramVoiceError("telegram_voice_archive_conflict")
            if (
                existing.sha256 != record.sha256
                or existing.size_bytes != record.size_bytes
                or existing.media_type != record.media_type
                or existing.account_id != account_id
                or existing.project_id != project_id
                or existing.direction != record.direction.value
            ):
                raise TelegramVoiceError("telegram_voice_archive_replay_mismatch")
            return _existing_result(existing)

    async def mark_recognition_started(self, archive_id: str, at: datetime) -> None:
        async with self._sessions.begin() as session:
            started = await session.scalar(
                update(TelegramVoiceArchive)
                .where(
                    TelegramVoiceArchive.id == uuid.UUID(archive_id),
                    TelegramVoiceArchive.transcription_status.is_(None),
                    TelegramVoiceArchive.recognition_started_at.is_(None),
                )
                .values(
                    recognition_started_at=at,
                    transcription_stopped_at=None,
                    transcription_stop_reason=None,
                )
                .returning(TelegramVoiceArchive.id)
            )
            if started is None:
                raise TelegramVoiceError("telegram_voice_recognition_already_claimed")

    async def mark_interrupted(
        self, archive_id: str, at: datetime, reason: str, started_at: datetime | None,
    ) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(TelegramVoiceArchive)
                .where(
                    TelegramVoiceArchive.id == uuid.UUID(archive_id),
                    TelegramVoiceArchive.transcription_status.is_(None),
                    TelegramVoiceArchive.transcription_stopped_at.is_(None),
                    TelegramVoiceArchive.recognition_started_at.is_not_distinct_from(started_at),
                )
                .values(transcription_stopped_at=at, transcription_stop_reason=reason)
            )

    async def persist_transcript(
        self,
        command: PersistTelegramVoiceTranscriptCommand,
    ) -> TelegramVoiceTranscriptPersistenceResult:
        try:
            archive_id = uuid.UUID(command.archive_id)
        except ValueError as exc:
            raise TelegramVoiceError(
                "telegram_voice_archive_scope_invalid"
            ) from exc

        async with self._sessions.begin() as session:
            archive = await session.scalar(
                select(TelegramVoiceArchive)
                .where(TelegramVoiceArchive.id == archive_id)
                .with_for_update()
            )
            if archive is None:
                raise TelegramVoiceError("telegram_voice_archive_not_found")

            expected = (
                command.status.value,
                command.text,
                command.language,
                command.model,
                command.reason_code,
            )
            if archive.transcription_status is not None:
                actual = (
                    archive.transcription_status,
                    archive.transcript_text,
                    archive.transcript_language,
                    archive.transcription_model,
                    archive.transcription_reason_code,
                )
                if actual != expected:
                    raise TelegramVoiceError(
                        "telegram_voice_transcript_replay_mismatch"
                    )
                return TelegramVoiceTranscriptPersistenceResult(
                    archive_id=str(archive.id),
                    duplicate=True,
                    status=command.status,
                )

            if archive.recognition_started_at != command.recognition_started_at:
                raise TelegramVoiceError("telegram_voice_recognition_claim_mismatch")
            archive.transcription_status = command.status.value
            archive.transcript_text = command.text
            archive.transcript_language = command.language
            archive.transcription_model = command.model
            archive.transcription_reason_code = command.reason_code
            archive.transcribed_at = command.transcribed_at
            archive.record_processing_result(
                command.transcribed_at,
                reason=None if command.text is not None else command.reason_code,
            )
            return TelegramVoiceTranscriptPersistenceResult(
                archive_id=str(archive.id),
                duplicate=False,
                status=command.status,
            )


def _existing_result(archive: TelegramVoiceArchive) -> TelegramVoiceArchiveResult:
    audio = None
    if archive.transcription_status is None:
        if len(archive.data) != archive.size_bytes or (
            hashlib.sha256(archive.data).hexdigest() != archive.sha256
        ):
            raise TelegramVoiceError("telegram_voice_archive_integrity_failed")
        audio = DownloadedVoice(
            data=archive.data, filename=archive.filename, media_type=archive.media_type,
        )
    return TelegramVoiceArchiveResult(
        archive_id=str(archive.id),
        duplicate=True,
        size_bytes=archive.size_bytes,
        media_type=archive.media_type,
        transcription_status=(
            TelegramVoiceTranscriptStatus(archive.transcription_status)
            if archive.transcription_status is not None
            else None
        ),
        transcript_text=archive.transcript_text,
        transcription_reason_code=archive.transcription_reason_code,
        archived_audio=audio,
    )
