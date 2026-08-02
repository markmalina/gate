"""PostgreSQL and Telegram adapters for controlled BECOME media intake."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
)
from app.application.become_media_intake import (
    BECOME_MEDIA_INTAKE_SCHEMA,
    CONFIRMED_TRANSCRIPT_RETENTION,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    AuthorizeBecomeMediaCommand,
    AuthorizedBecomeMediaIntake,
    BecomeMediaEvidence,
    DownloadedBecomeMedia,
    PersistBecomeRawMediaCommand,
    PersistedBecomeRawMedia,
    media_evidence,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeMediaIntakeModel,
    BecomeRelationsConsentHandoffModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.models.domain import TelegramReceipt


class TelegramBecomeMediaDownloader:
    """Download one bounded file with only the isolated BECOME bot token."""

    def __init__(
        self,
        *,
        bot_token: str,
        http_client: httpx.AsyncClient,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if not bot_token:
            raise BecomeControlledIntakeError(
                "become_media_bot_token_required"
            )
        if not api_base_url.startswith("https://"):
            raise BecomeControlledIntakeError(
                "become_media_api_url_invalid"
            )
        self._bot_token = bot_token
        self._http = http_client
        self._api = api_base_url.rstrip("/")

    async def download(
        self,
        *,
        evidence: BecomeMediaEvidence,
    ) -> DownloadedBecomeMedia:
        try:
            metadata_response = await self._http.post(
                f"{self._api}/bot{self._bot_token}/getFile",
                json={"file_id": evidence.provider_file_id},
                timeout=15.0,
            )
            metadata = metadata_response.json()
        except (httpx.HTTPError, ValueError):
            raise BecomeControlledIntakeError(
                "become_media_get_file_failed"
            ) from None
        result = _telegram_file_result(metadata_response, metadata)
        file_size = result.get("file_size")
        if (
            isinstance(file_size, int)
            and file_size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES
        ):
            raise BecomeControlledIntakeError(
                "become_media_download_limit_exceeded"
            )
        file_path = result.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise BecomeControlledIntakeError(
                "become_media_file_path_missing"
            )
        path = PurePosixPath(file_path.strip().lstrip("/"))
        if any(part in {".", ".."} for part in path.parts):
            raise BecomeControlledIntakeError(
                "become_media_file_path_invalid"
            )
        safe_path = quote(path.as_posix(), safe="/")
        default_type = (
            "audio/ogg" if evidence.kind.value == "voice" else "video/mp4"
        )
        try:
            data = bytearray()
            media_type = default_type
            async with self._http.stream(
                "GET",
                (
                    f"{self._api}/file/bot{self._bot_token}/"
                    f"{safe_path}"
                ),
                timeout=30.0,
            ) as response:
                response.raise_for_status()
                media_type = response.headers.get(
                    "content-type",
                    default_type,
                ).split(";", 1)[0]
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if (
                        len(data)
                        > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES
                    ):
                        raise BecomeControlledIntakeError(
                            "become_media_download_limit_exceeded"
                        )
        except httpx.HTTPError:
            raise BecomeControlledIntakeError(
                "become_media_download_failed"
            ) from None
        return DownloadedBecomeMedia(
            data=bytes(data),
            media_type=media_type,
        )


def _telegram_file_result(
    response: httpx.Response,
    payload: Any,
) -> dict[str, Any]:
    if (
        response.is_error
        or not isinstance(payload, dict)
        or payload.get("ok") is not True
    ):
        raise BecomeControlledIntakeError(
            "become_media_get_file_rejected"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BecomeControlledIntakeError(
            "become_media_get_file_invalid"
        )
    return result


class SessionFactoryBecomeMediaStore:
    """Authorize against the executed consent chain, then persist raw bytes."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def installation_ref(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
    ) -> str | None:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentInstallationModel.installation_key)
                    .select_from(Workspace)
                    .join(
                        TransportConnector,
                        TransportConnector.workspace_id == Workspace.id,
                    )
                    .join(
                        Dialog,
                        (Dialog.workspace_id == Workspace.id)
                        & (
                            Dialog.connector_id
                            == TransportConnector.id
                        ),
                    )
                    .join(
                        AgentInstallationModel,
                        (
                            AgentInstallationModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            AgentInstallationModel.connector_id
                            == TransportConnector.id
                        )
                        & (
                            AgentInstallationModel.dialog_id
                            == Dialog.id
                        ),
                    )
                    .where(
                        Workspace.key == workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_key
                        == BECOME_CONNECTOR_KEY,
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id == provider_dialog_id,
                        AgentInstallationModel.state == "active",
                    )
                    .limit(2)
                )
            ).all()
        if len(rows) != 1:
            return None
        return str(rows[0])

    async def authorize(
        self,
        command: AuthorizeBecomeMediaCommand,
    ) -> AuthorizedBecomeMediaIntake:
        try:
            source_receipt_id = uuid.UUID(command.source_receipt_id)
        except ValueError as exc:
            raise BecomeControlledIntakeError(
                "become_media_source_receipt_invalid"
            ) from exc
        if (
            len(command.file_unique_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in command.file_unique_digest
            )
        ):
            raise BecomeControlledIntakeError(
                "become_media_file_digest_invalid"
            )

        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        TransportConnector,
                        Dialog,
                        AgentInstallationModel,
                        TelegramReceipt,
                        BecomeControlledIntakeReceiptModel,
                        BecomeControlledConsentReceiptModel,
                        BecomeRelationsConsentHandoffModel,
                    )
                    .join(
                        TransportConnector,
                        TransportConnector.workspace_id == Workspace.id,
                    )
                    .join(
                        Dialog,
                        (Dialog.workspace_id == Workspace.id)
                        & (
                            Dialog.connector_id
                            == TransportConnector.id
                        ),
                    )
                    .join(
                        AgentInstallationModel,
                        (
                            AgentInstallationModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            AgentInstallationModel.connector_id
                            == TransportConnector.id
                        )
                        & (
                            AgentInstallationModel.dialog_id
                            == Dialog.id
                        ),
                    )
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == source_receipt_id,
                    )
                    .join(
                        BecomeControlledIntakeReceiptModel,
                        (
                            BecomeControlledIntakeReceiptModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            BecomeControlledIntakeReceiptModel.installation_id
                            == AgentInstallationModel.id
                        ),
                    )
                    .join(
                        BecomeControlledConsentReceiptModel,
                        (
                            BecomeControlledConsentReceiptModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            BecomeControlledConsentReceiptModel.installation_id
                            == AgentInstallationModel.id
                        )
                        & (
                            BecomeControlledConsentReceiptModel.subject_ref
                            == command.subject_ref
                        ),
                    )
                    .join(
                        BecomeRelationsConsentHandoffModel,
                        (
                            BecomeRelationsConsentHandoffModel.consent_receipt_id
                            == BecomeControlledConsentReceiptModel.id
                        ),
                    )
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_key
                        == BECOME_CONNECTOR_KEY,
                        TransportConnector.connector_kind
                        == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id
                        == command.provider_dialog_id,
                        AgentInstallationModel.state == "active",
                        TelegramReceipt.bot_key
                        == BECOME_CONNECTOR_KEY,
                        BecomeControlledIntakeReceiptModel.intent
                        == "registration_confirmed",
                        BecomeControlledIntakeReceiptModel.subject_ref
                        == command.subject_ref,
                        BecomeControlledIntakeReceiptModel.group_state
                        == "owner_only",
                        BecomeControlledIntakeReceiptModel.member_count == 2,
                        BecomeControlledConsentReceiptModel.intent
                        == "consent_accepted",
                        BecomeControlledConsentReceiptModel.group_state
                        == "owner_only",
                        BecomeControlledConsentReceiptModel.member_count == 2,
                        BecomeRelationsConsentHandoffModel.status
                        == "executed",
                    )
                    .with_for_update(
                        of=BecomeRelationsConsentHandoffModel
                    )
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_media_executed_consent_required"
                )
            (
                workspace,
                _connector,
                _dialog,
                installation,
                source,
                registration,
                consent,
                handoff,
            ) = row
            source_evidence = media_evidence(source.payload)
            if (
                source_evidence.provider_dialog_id
                != command.provider_dialog_id
                or source_evidence.kind != command.kind
                or hashlib.sha256(
                    source_evidence.provider_file_unique_id.encode(
                        "utf-8"
                    )
                ).hexdigest()
                != command.file_unique_digest
            ):
                raise BecomeControlledIntakeError(
                    "become_media_source_mismatch"
                )

            intake_key = (
                "become_media_"
                + hashlib.sha256(
                    (
                        command.source_receipt_id
                        + ":"
                        + command.file_unique_digest
                    ).encode("utf-8")
                ).hexdigest()[:24]
            )
            intake_id = uuid.uuid4()
            inserted = await session.scalar(
                insert(BecomeMediaIntakeModel)
                .values(
                    id=intake_id,
                    workspace_id=workspace.id,
                    installation_id=installation.id,
                    source_receipt_id=source.id,
                    registration_receipt_id=registration.id,
                    consent_receipt_id=consent.id,
                    relations_handoff_id=handoff.id,
                    intake_key=intake_key,
                    schema_name=BECOME_MEDIA_INTAKE_SCHEMA,
                    subject_ref=command.subject_ref,
                    update_ref=command.update_ref,
                    kind=command.kind.value,
                    file_unique_digest=command.file_unique_digest,
                    duration_seconds=command.duration_seconds,
                    declared_size_bytes=command.declared_size_bytes,
                    status="pending_download",
                    raw_expires_at=command.raw_expires_at,
                    transcript_state="none",
                    received_at=command.received_at,
                )
                .on_conflict_do_nothing(
                    index_elements=["source_receipt_id"]
                )
                .returning(BecomeMediaIntakeModel.id)
            )
            if inserted is not None:
                return AuthorizedBecomeMediaIntake(
                    intake_id=str(inserted),
                    intake_key=intake_key,
                    duplicate=False,
                    archived=False,
                )

            existing = await session.scalar(
                select(BecomeMediaIntakeModel).where(
                    BecomeMediaIntakeModel.source_receipt_id == source.id
                )
            )
            if (
                existing is None
                or existing.workspace_id != workspace.id
                or existing.installation_id != installation.id
                or existing.registration_receipt_id != registration.id
                or existing.consent_receipt_id != consent.id
                or existing.relations_handoff_id != handoff.id
                or existing.intake_key != intake_key
                or existing.subject_ref != command.subject_ref
                or existing.update_ref != command.update_ref
                or existing.kind != command.kind.value
                or existing.file_unique_digest
                != command.file_unique_digest
                or existing.duration_seconds
                != command.duration_seconds
                or existing.declared_size_bytes
                != command.declared_size_bytes
                or existing.raw_expires_at
                != command.raw_expires_at
            ):
                raise BecomeControlledIntakeError(
                    "become_media_mutated_replay"
                )
            return AuthorizedBecomeMediaIntake(
                intake_id=str(existing.id),
                intake_key=existing.intake_key,
                duplicate=True,
                archived=existing.status
                in {"archived", "raw_purged"},
            )

    async def persist_raw(
        self,
        command: PersistBecomeRawMediaCommand,
    ) -> PersistedBecomeRawMedia:
        try:
            intake_id = uuid.UUID(command.intake_id)
        except ValueError as exc:
            raise BecomeControlledIntakeError(
                "become_media_intake_id_invalid"
            ) from exc
        if hashlib.sha256(command.data).hexdigest() != command.sha256:
            raise BecomeControlledIntakeError(
                "become_media_raw_digest_mismatch"
            )
        if (
            not command.data
            or len(command.data)
            > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES
        ):
            raise BecomeControlledIntakeError(
                "become_media_download_limit_exceeded"
            )

        async with self._sessions.begin() as session:
            intake = await session.scalar(
                select(BecomeMediaIntakeModel)
                .where(BecomeMediaIntakeModel.id == intake_id)
                .with_for_update()
            )
            if intake is None:
                raise BecomeControlledIntakeError(
                    "become_media_intake_not_found"
                )
            if intake.status == "pending_download":
                intake.status = "archived"
                intake.raw_data = command.data
                intake.raw_sha256 = command.sha256
                intake.raw_size_bytes = len(command.data)
                intake.raw_media_type = command.media_type
                intake.raw_expires_at = command.raw_expires_at
                intake.updated_at = datetime.now(
                    command.raw_expires_at.tzinfo
                )
                return PersistedBecomeRawMedia(
                    intake_id=str(intake.id),
                    duplicate=False,
                    size_bytes=len(command.data),
                )
            if (
                intake.status == "archived"
                and intake.raw_sha256 == command.sha256
                and intake.raw_size_bytes == len(command.data)
                and intake.raw_media_type == command.media_type
                and intake.raw_expires_at == command.raw_expires_at
            ):
                return PersistedBecomeRawMedia(
                    intake_id=str(intake.id),
                    duplicate=True,
                    size_bytes=intake.raw_size_bytes,
                )
            raise BecomeControlledIntakeError(
                "become_media_raw_replay_mismatch"
            )

    async def confirm_transcript(
        self,
        *,
        intake_id: str,
        text: str,
        confirmed_at: datetime,
    ) -> bool:
        try:
            parsed_id = uuid.UUID(intake_id)
        except ValueError as exc:
            raise BecomeControlledIntakeError(
                "become_transcript_intake_id_invalid"
            ) from exc
        normalized = text.strip()
        if (
            not normalized
            or normalized != text
            or len(normalized) > 100_000
            or confirmed_at.tzinfo is None
        ):
            raise BecomeControlledIntakeError(
                "become_transcript_confirmation_invalid"
            )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        expires_at = confirmed_at + CONFIRMED_TRANSCRIPT_RETENTION
        async with self._sessions.begin() as session:
            intake = await session.scalar(
                select(BecomeMediaIntakeModel)
                .where(BecomeMediaIntakeModel.id == parsed_id)
                .with_for_update()
            )
            if intake is None or intake.status == "pending_download":
                raise BecomeControlledIntakeError(
                    "become_transcript_raw_archive_required"
                )
            if intake.transcript_state == "none":
                intake.transcript_state = "confirmed"
                intake.confirmed_transcript = normalized
                intake.transcript_digest = digest
                intake.transcript_confirmed_at = confirmed_at
                intake.transcript_expires_at = expires_at
                intake.updated_at = confirmed_at
                return False
            if (
                intake.transcript_state == "confirmed"
                and intake.confirmed_transcript == normalized
                and intake.transcript_digest == digest
                and intake.transcript_confirmed_at == confirmed_at
                and intake.transcript_expires_at == expires_at
            ):
                return True
            raise BecomeControlledIntakeError(
                "become_transcript_mutated_replay"
            )


@dataclass(frozen=True, slots=True)
class BecomeMediaRetentionResult:
    raw_purged: int
    transcripts_purged: int


class SessionFactoryBecomeMediaRetention:
    """Purge content while preserving content-free audit identities."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def purge_expired(
        self,
        *,
        now: datetime,
    ) -> BecomeMediaRetentionResult:
        if now.tzinfo is None:
            raise ValueError("become_media_retention_time_invalid")
        async with self._sessions.begin() as session:
            raw_result = await session.execute(
                update(BecomeMediaIntakeModel)
                .where(
                    BecomeMediaIntakeModel.status == "archived",
                    BecomeMediaIntakeModel.raw_expires_at <= now,
                )
                .values(
                    status="raw_purged",
                    raw_data=None,
                    raw_purged_at=now,
                    updated_at=now,
                )
            )
            transcript_result = await session.execute(
                update(BecomeMediaIntakeModel)
                .where(
                    BecomeMediaIntakeModel.transcript_state
                    == "confirmed",
                    BecomeMediaIntakeModel.transcript_expires_at
                    <= now,
                )
                .values(
                    transcript_state="purged",
                    confirmed_transcript=None,
                    transcript_purged_at=now,
                    updated_at=now,
                )
            )
            return BecomeMediaRetentionResult(
                raw_purged=int(raw_result.rowcount or 0),
                transcripts_purged=int(
                    transcript_result.rowcount or 0
                ),
            )


async def become_media_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, int]:
    row = (
        await session.execute(
            select(
                func.count(BecomeMediaIntakeModel.id),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.status == "pending_download"
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.status == "archived"
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.status == "raw_purged"
                ),
                func.coalesce(
                    func.sum(BecomeMediaIntakeModel.raw_size_bytes).filter(
                        BecomeMediaIntakeModel.status == "archived"
                    ),
                    0,
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.transcript_state
                    == "confirmed"
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.transcript_state == "purged"
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.kind == "voice"
                ),
                func.count(BecomeMediaIntakeModel.id).filter(
                    BecomeMediaIntakeModel.kind == "video"
                ),
            )
            .join(
                Workspace,
                Workspace.id == BecomeMediaIntakeModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
            )
        )
    ).one()
    return {
        "media_intake_count": int(row[0] or 0),
        "media_pending_download_count": int(row[1] or 0),
        "raw_media_retained_count": int(row[2] or 0),
        "raw_media_purged_count": int(row[3] or 0),
        "raw_media_retained_bytes": int(row[4] or 0),
        "confirmed_transcript_retained_count": int(row[5] or 0),
        "confirmed_transcript_purged_count": int(row[6] or 0),
        "voice_intake_count": int(row[7] or 0),
        "video_intake_count": int(row[8] or 0),
    }
