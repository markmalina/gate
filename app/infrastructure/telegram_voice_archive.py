"""PostgreSQL persistence for bounded Telegram Business voice audio."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.telegram_voice_archive import (
    TelegramVoiceArchiveRecord,
    TelegramVoiceArchiveResult,
    TelegramVoiceArchiveStore,
)
from app.application.telegram_voice_transcription import TelegramVoiceError
from app.models.domain import TelegramVoiceArchive


class SessionFactoryTelegramVoiceArchiveStore(TelegramVoiceArchiveStore):
    """Persist one voice idempotently in a short independent transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

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
            return TelegramVoiceArchiveResult(
                archive_id=str(existing.id),
                duplicate=True,
                size_bytes=existing.size_bytes,
                media_type=existing.media_type,
            )
