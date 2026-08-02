"""Controlled BECOME-only voice/video intake and retention contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
    opaque_subject_ref,
    opaque_update_ref,
)

JsonObject = dict[str, Any]

BECOME_MEDIA_INTAKE_SCHEMA = "gate.become-media-intake@1"
RAW_MEDIA_RETENTION = timedelta(days=7)
CONFIRMED_TRANSCRIPT_RETENTION = timedelta(days=30)
TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES = 20_000_000


class BecomeMediaKind(StrEnum):
    VOICE = "voice"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class BecomeMediaEvidence:
    provider_dialog_id: str = field(repr=False)
    provider_actor_id: str = field(repr=False)
    provider_file_id: str = field(repr=False)
    provider_file_unique_id: str = field(repr=False)
    update_id: int
    message_id: int
    kind: BecomeMediaKind
    duration_seconds: int
    declared_size_bytes: int | None
    declared_media_type: str | None


@dataclass(frozen=True, slots=True)
class DownloadedBecomeMedia:
    data: bytes = field(repr=False)
    media_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise BecomeControlledIntakeError("become_media_download_empty")
        if len(self.data) > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            raise BecomeControlledIntakeError("become_media_download_limit_exceeded")
        if (
            not self.media_type
            or self.media_type != self.media_type.strip()
            or len(self.media_type) > 128
        ):
            raise BecomeControlledIntakeError("become_media_type_invalid")


@dataclass(frozen=True, slots=True)
class AuthorizeBecomeMediaCommand:
    workspace_key: str
    source_receipt_id: str
    provider_dialog_id: str = field(repr=False)
    subject_ref: str
    update_ref: str
    kind: BecomeMediaKind
    file_unique_digest: str
    duration_seconds: int
    declared_size_bytes: int | None
    received_at: datetime
    raw_expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizedBecomeMediaIntake:
    intake_id: str
    intake_key: str
    duplicate: bool
    archived: bool


@dataclass(frozen=True, slots=True)
class PersistBecomeRawMediaCommand:
    intake_id: str
    data: bytes = field(repr=False)
    media_type: str
    sha256: str
    raw_expires_at: datetime


@dataclass(frozen=True, slots=True)
class PersistedBecomeRawMedia:
    intake_id: str
    duplicate: bool
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessBecomeMediaCommand:
    workspace_key: str
    source_receipt_id: str
    update: JsonObject = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessedBecomeMedia:
    intake_key: str
    kind: BecomeMediaKind
    duplicate: bool
    archived: bool
    size_bytes: int


class BecomeMediaStore(Protocol):
    async def installation_ref(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
    ) -> str | None: ...

    async def authorize(
        self,
        command: AuthorizeBecomeMediaCommand,
    ) -> AuthorizedBecomeMediaIntake: ...

    async def persist_raw(
        self,
        command: PersistBecomeRawMediaCommand,
    ) -> PersistedBecomeRawMedia: ...


class BecomeMediaDownloader(Protocol):
    async def download(
        self,
        *,
        evidence: BecomeMediaEvidence,
    ) -> DownloadedBecomeMedia: ...


class BecomeMediaPresenceReader(Protocol):
    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None: ...


class ProcessBecomeMediaService:
    """Archive one consent-bound owner-only media update without model effects."""

    def __init__(
        self,
        *,
        store: BecomeMediaStore,
        downloader: BecomeMediaDownloader,
        presence: BecomeMediaPresenceReader,
        subject_pepper: str,
    ) -> None:
        if not subject_pepper:
            raise BecomeControlledIntakeError(
                "become_media_subject_scope_incomplete"
            )
        self._store = store
        self._downloader = downloader
        self._presence = presence
        self._subject_pepper = subject_pepper

    async def execute(
        self,
        command: ProcessBecomeMediaCommand,
    ) -> ProcessedBecomeMedia:
        if command.received_at.tzinfo is None:
            raise BecomeControlledIntakeError("become_media_timestamp_invalid")
        evidence = media_evidence(command.update)
        installation_ref = await self._store.installation_ref(
            workspace_key=command.workspace_key,
            provider_dialog_id=evidence.provider_dialog_id,
        )
        if (
            installation_ref is None
            or not installation_ref.startswith("installation-")
        ):
            raise BecomeControlledIntakeError(
                "become_media_installation_invalid"
            )
        member_count = await self._presence.member_count(
            installation_ref=installation_ref,
            provider_dialog_id=evidence.provider_dialog_id,
        )
        if member_count != 2:
            raise BecomeControlledIntakeError(
                "become_media_owner_only_required"
            )
        if (
            evidence.declared_size_bytes is not None
            and evidence.declared_size_bytes
            > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES
        ):
            raise BecomeControlledIntakeError(
                "become_media_download_limit_exceeded"
            )

        subject_ref = opaque_subject_ref(
            provider_actor_id=evidence.provider_actor_id,
            pepper=self._subject_pepper,
        )
        received_at = command.received_at.astimezone(UTC)
        raw_expires_at = received_at + RAW_MEDIA_RETENTION
        authorized = await self._store.authorize(
            AuthorizeBecomeMediaCommand(
                workspace_key=command.workspace_key,
                source_receipt_id=command.source_receipt_id,
                provider_dialog_id=evidence.provider_dialog_id,
                subject_ref=subject_ref,
                update_ref=opaque_update_ref(
                    source_receipt_id=command.source_receipt_id
                ),
                kind=evidence.kind,
                file_unique_digest=_digest_token(
                    evidence.provider_file_unique_id
                ),
                duration_seconds=evidence.duration_seconds,
                declared_size_bytes=evidence.declared_size_bytes,
                received_at=received_at,
                raw_expires_at=raw_expires_at,
            )
        )
        if authorized.archived:
            return ProcessedBecomeMedia(
                intake_key=authorized.intake_key,
                kind=evidence.kind,
                duplicate=True,
                archived=True,
                size_bytes=0,
            )

        downloaded = await self._downloader.download(evidence=evidence)
        media_type = _normalized_media_type(
            kind=evidence.kind,
            downloaded=downloaded.media_type,
            declared=evidence.declared_media_type,
        )
        raw = await self._store.persist_raw(
            PersistBecomeRawMediaCommand(
                intake_id=authorized.intake_id,
                data=downloaded.data,
                media_type=media_type,
                sha256=hashlib.sha256(downloaded.data).hexdigest(),
                raw_expires_at=raw_expires_at,
            )
        )
        return ProcessedBecomeMedia(
            intake_key=authorized.intake_key,
            kind=evidence.kind,
            duplicate=authorized.duplicate and raw.duplicate,
            archived=True,
            size_bytes=raw.size_bytes,
        )


def is_become_media_update(update: JsonObject) -> bool:
    message = update.get("message")
    return isinstance(message, dict) and (
        "voice" in message or "video" in message
    )


def media_evidence(update: JsonObject) -> BecomeMediaEvidence:
    """Accept one human voice/video message in one Telegram group."""

    if (
        set(update) != {"update_id", "message"}
        or type(update.get("update_id")) is not int
    ):
        raise BecomeControlledIntakeError("become_media_update_shape_invalid")
    update_id = update["update_id"]
    if update_id < 0:
        raise BecomeControlledIntakeError("become_media_update_id_invalid")
    message = update.get("message")
    if not isinstance(message, dict):
        raise BecomeControlledIntakeError("become_media_message_missing")
    chat = message.get("chat")
    actor = message.get("from")
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
    ):
        raise BecomeControlledIntakeError("become_media_group_required")
    if not isinstance(actor, dict) or actor.get("is_bot") is not False:
        raise BecomeControlledIntakeError("become_media_human_required")
    chat_id = _provider_id(
        chat.get("id"),
        "become_media_dialog_invalid",
    )
    actor_id = _provider_id(
        actor.get("id"),
        "become_media_actor_invalid",
    )
    message_id = message.get("message_id")
    if type(message_id) is not int or message_id <= 0:
        raise BecomeControlledIntakeError("become_media_message_id_invalid")

    media_keys = [key for key in ("voice", "video") if key in message]
    if len(media_keys) != 1:
        raise BecomeControlledIntakeError("become_media_kind_ambiguous")
    media_key = media_keys[0]
    raw_media = message.get(media_key)
    if not isinstance(raw_media, dict):
        raise BecomeControlledIntakeError("become_media_payload_invalid")

    file_id = _token(
        raw_media.get("file_id"),
        "become_media_file_id_invalid",
        512,
    )
    file_unique_id = _token(
        raw_media.get("file_unique_id"),
        "become_media_file_unique_id_invalid",
        512,
    )
    duration = raw_media.get("duration")
    if type(duration) is not int or duration <= 0:
        raise BecomeControlledIntakeError("become_media_duration_invalid")
    file_size = raw_media.get("file_size")
    if file_size is not None and (
        type(file_size) is not int or file_size <= 0
    ):
        raise BecomeControlledIntakeError("become_media_size_invalid")
    media_type = raw_media.get("mime_type")
    if media_type is not None:
        media_type = _token(
            media_type,
            "become_media_declared_type_invalid",
            128,
        )

    return BecomeMediaEvidence(
        provider_dialog_id=chat_id,
        provider_actor_id=actor_id,
        provider_file_id=file_id,
        provider_file_unique_id=file_unique_id,
        update_id=update_id,
        message_id=message_id,
        kind=BecomeMediaKind(media_key),
        duration_seconds=duration,
        declared_size_bytes=file_size,
        declared_media_type=media_type,
    )


def _normalized_media_type(
    *,
    kind: BecomeMediaKind,
    downloaded: str,
    declared: str | None,
) -> str:
    prefix = "audio/" if kind is BecomeMediaKind.VOICE else "video/"
    for candidate in (downloaded, declared):
        if (
            isinstance(candidate, str)
            and candidate.startswith(prefix)
            and len(candidate) <= 128
        ):
            return candidate
    return "audio/ogg" if kind is BecomeMediaKind.VOICE else "video/mp4"


def _digest_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_id(value: object, code: str) -> str:
    if (
        not isinstance(value, (str, int))
        or isinstance(value, bool)
        or not str(value)
    ):
        raise BecomeControlledIntakeError(code)
    return str(value)


def _token(value: object, code: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise BecomeControlledIntakeError(code)
    return value
