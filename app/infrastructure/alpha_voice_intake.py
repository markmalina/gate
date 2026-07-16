"""PostgreSQL queue adapter for dormant Alpha voice intake.

This adapter persists sanitized metadata and one queued job.  It deliberately does not
download files, invoke a transcription provider, or send a Telegram message.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_voice import TRANSCRIPTION_OPERATION, AlphaVoiceIntakeCode
from app.application.alpha_voice_intake import (
    AlphaVoiceQueueCode,
    AlphaVoiceQueueDisposition,
    AlphaVoiceQueueResult,
    QueueAlphaVoiceJobCommand,
)
from app.models.alpha import AgentInstallationModel
from app.models.alpha_voice import AlphaVoiceJobModel, CanonicalVoiceMediaModel
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace

_FILE_UNIQUE_ID_HASH_DOMAIN = b"gate.telegram.voice.file_unique_id.v1\x00"


def hash_telegram_file_unique_id(file_unique_id: str) -> str:
    """Return a domain-separated digest; callers must never persist the raw value."""

    return hashlib.sha256(
        _FILE_UNIQUE_ID_HASH_DOMAIN + file_unique_id.encode("utf-8")
    ).hexdigest()


class SqlAlphaVoiceQueueRepository:
    """Queue one eligible voice job inside the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue_once(
        self, command: QueueAlphaVoiceJobCommand
    ) -> AlphaVoiceQueueResult:
        workspace = await self._session.scalar(
            select(Workspace).where(Workspace.key == command.job_key.workspace_key)
        )
        if workspace is None or workspace.is_active is not True:
            return _terminal(AlphaVoiceQueueCode.WORKSPACE_UNAVAILABLE)

        installation_row = (
            await self._session.execute(
                select(AgentInstallationModel, Dialog, TransportConnector)
                .join(
                    Dialog,
                    (Dialog.id == AgentInstallationModel.dialog_id)
                    & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
                )
                .join(
                    TransportConnector,
                    (TransportConnector.id == AgentInstallationModel.connector_id)
                    & (
                        TransportConnector.workspace_id
                        == AgentInstallationModel.workspace_id
                    ),
                )
                .where(
                    AgentInstallationModel.workspace_id == workspace.id,
                    AgentInstallationModel.installation_key == command.installation_ref,
                )
                .with_for_update(of=AgentInstallationModel)
            )
        ).one_or_none()
        if installation_row is None:
            return _terminal(AlphaVoiceQueueCode.INSTALLATION_UNAVAILABLE)
        installation, dialog, connector = installation_row

        if installation.state != "active":
            return _terminal(AlphaVoiceQueueCode.INSTALLATION_INACTIVE)
        if connector.is_enabled is not True or connector.connector_kind != "telegram_bot":
            return _terminal(AlphaVoiceQueueCode.CONNECTOR_INACTIVE)
        if dialog.dialog_kind != command.dialog_kind.value:
            return _terminal(AlphaVoiceQueueCode.DIALOG_SCOPE_MISMATCH)

        try:
            canonical_message_id = uuid.UUID(command.job_key.canonical_message_id)
        except ValueError:
            return _terminal(AlphaVoiceQueueCode.MESSAGE_UNAVAILABLE)
        message = await self._session.scalar(
            select(CanonicalMessage).where(
                CanonicalMessage.id == canonical_message_id,
                CanonicalMessage.workspace_id == workspace.id,
            )
        )
        if message is None:
            return _terminal(AlphaVoiceQueueCode.MESSAGE_UNAVAILABLE)
        if message.dialog_id != dialog.id:
            return _terminal(AlphaVoiceQueueCode.DIALOG_SCOPE_MISMATCH)
        if message.direction != "inbound":
            return _terminal(AlphaVoiceQueueCode.MESSAGE_NOT_INBOUND)

        metadata = command.metadata
        mime_type = _canonical_mime_type(metadata.mime_type)
        unique_hash = hash_telegram_file_unique_id(metadata.file_unique_id)
        proposed_media_id = uuid.uuid4()
        await self._session.scalar(
            insert(CanonicalVoiceMediaModel)
            .values(
                id=proposed_media_id,
                workspace_id=workspace.id,
                dialog_id=dialog.id,
                canonical_message_id=message.id,
                provider_file_id=metadata.file_id,
                file_unique_id_sha256=unique_hash,
                duration_seconds=metadata.duration_seconds,
                file_size_bytes=metadata.file_size_bytes,
                mime_type=mime_type,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    CanonicalVoiceMediaModel.workspace_id,
                    CanonicalVoiceMediaModel.canonical_message_id,
                ]
            )
            .returning(CanonicalVoiceMediaModel.id)
        )
        media = await self._session.scalar(
            select(CanonicalVoiceMediaModel).where(
                CanonicalVoiceMediaModel.workspace_id == workspace.id,
                CanonicalVoiceMediaModel.canonical_message_id == message.id,
            )
        )
        if media is None:
            raise RuntimeError("voice media upsert did not return a durable row")
        if not _same_media(
            media,
            provider_file_id=metadata.file_id,
            unique_hash=unique_hash,
            duration_seconds=metadata.duration_seconds,
            file_size_bytes=metadata.file_size_bytes,
            mime_type=mime_type,
        ):
            return _terminal(AlphaVoiceQueueCode.MEDIA_CONFLICT)

        proposed_job_id = uuid.uuid4()
        inserted_job_id = await self._session.scalar(
            insert(AlphaVoiceJobModel)
            .values(
                id=proposed_job_id,
                workspace_id=workspace.id,
                dialog_id=dialog.id,
                installation_id=installation.id,
                canonical_message_id=message.id,
                media_id=media.id,
                operation=TRANSCRIPTION_OPERATION,
                state="queued",
                attempts=0,
                version=1,
                intake_code=AlphaVoiceIntakeCode.QUEUED.value,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AlphaVoiceJobModel.workspace_id,
                    AlphaVoiceJobModel.canonical_message_id,
                    AlphaVoiceJobModel.operation,
                ]
            )
            .returning(AlphaVoiceJobModel.id)
        )
        if inserted_job_id is not None:
            return AlphaVoiceQueueResult(
                disposition=AlphaVoiceQueueDisposition.QUEUED,
                code=AlphaVoiceQueueCode.QUEUED,
                job_id=str(inserted_job_id),
            )

        existing_job_id = await self._session.scalar(
            select(AlphaVoiceJobModel.id).where(
                AlphaVoiceJobModel.workspace_id == workspace.id,
                AlphaVoiceJobModel.canonical_message_id == message.id,
                AlphaVoiceJobModel.operation == TRANSCRIPTION_OPERATION,
            )
        )
        if existing_job_id is None:
            raise RuntimeError("voice job upsert did not return a durable row")
        return AlphaVoiceQueueResult(
            disposition=AlphaVoiceQueueDisposition.DUPLICATE,
            code=AlphaVoiceQueueCode.DUPLICATE,
            job_id=str(existing_job_id),
        )


def _same_media(
    media: CanonicalVoiceMediaModel,
    *,
    provider_file_id: str,
    unique_hash: str,
    duration_seconds: int,
    file_size_bytes: int | None,
    mime_type: str | None,
) -> bool:
    return (
        media.provider_file_id == provider_file_id
        and media.file_unique_id_sha256 == unique_hash
        and media.duration_seconds == duration_seconds
        and media.file_size_bytes == file_size_bytes
        and media.mime_type == mime_type
    )


def _canonical_mime_type(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split(";", 1)[0].strip().casefold()


def _terminal(code: AlphaVoiceQueueCode) -> AlphaVoiceQueueResult:
    return AlphaVoiceQueueResult(
        disposition=AlphaVoiceQueueDisposition.TERMINAL,
        code=code,
    )
