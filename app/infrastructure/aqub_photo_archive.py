"""PostgreSQL job-ledger adapter for the default-off AQUB photo archive."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.aqub_photo_archive import (
    AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS,
    AQUB_PHOTO_ARCHIVE_STALE_AFTER,
    TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES,
    AQUBPhotoArchiveClaim,
    AQUBPhotoArchiveCompletion,
    AQUBPhotoArchiveError,
    AQUBPhotoArchiveStatus,
    AQUBPhotoDiscoveryCursor,
    DownloadedAQUBPhoto,
    require_aware_utc,
    retry_delay,
)
from app.application.aqub_telegram_projection import project_aqub_telegram_inbound
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBPhotoReference,
    AQUBTransportContentKind,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.aqub import AQUBInstallationBinding, AQUBPhotoArchive
from app.models.domain import TelegramReceipt, TelegramReceiptStatus


@dataclass(frozen=True, slots=True)
class _ValidatedSource:
    photo: AQUBPhotoReference
    payload_sha256: str


class SqlAQUBPhotoArchiveRepository:
    """Discover terminal photo receipts without changing their lifecycle."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def discover(
        self,
        *,
        not_before: datetime,
        cursor: AQUBPhotoDiscoveryCursor | None,
        limit: int,
        observed_at: datetime,
    ) -> AQUBPhotoDiscoveryCursor | None:
        cutoff = require_aware_utc(not_before, code="aqub_photo_cutoff_invalid")
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        if not 1 <= limit <= 100:
            raise ValueError("aqub_photo_discovery_limit_invalid")

        async with self._sessions.begin() as session:
            statement = (
                select(TelegramReceipt, AQUBInstallationBinding)
                .join(
                    AQUBInstallationBinding,
                    AQUBInstallationBinding.bot_key == TelegramReceipt.bot_key,
                )
                .outerjoin(
                    AQUBPhotoArchive,
                    AQUBPhotoArchive.receipt_id == TelegramReceipt.id,
                )
                .where(
                    TelegramReceipt.bot_key == AQUB_RECEIPT_BOT_KEY,
                    TelegramReceipt.status == TelegramReceiptStatus.PROCESSED.value,
                    TelegramReceipt.processed_at.is_not(None),
                    TelegramReceipt.received_at >= cutoff,
                    func.jsonb_typeof(
                        TelegramReceipt.payload["message"]["photo"]
                    )
                    == "array",
                    AQUBPhotoArchive.id.is_(None),
                )
                .order_by(TelegramReceipt.received_at, TelegramReceipt.id)
                .limit(limit)
                .with_for_update(of=TelegramReceipt, skip_locked=True)
            )
            if cursor is not None:
                cursor_time = require_aware_utc(
                    cursor.received_at,
                    code="aqub_photo_cursor_invalid",
                )
                statement = statement.where(
                    or_(
                        TelegramReceipt.received_at > cursor_time,
                        and_(
                            TelegramReceipt.received_at == cursor_time,
                            TelegramReceipt.id > cursor.receipt_id,
                        ),
                    )
                )
            rows = (await session.execute(statement)).all()
            for receipt, binding in rows:
                source, error_code = _validated_source(
                    receipt=receipt,
                    binding=binding,
                    not_before=cutoff,
                )
                source_payload_sha256 = canonical_telegram_payload_digest(
                    receipt.payload
                )
                values: dict[str, Any] = {
                    "id": uuid4(),
                    "receipt_id": receipt.id,
                    "binding_id": binding.id,
                    "source_payload_sha256": source_payload_sha256,
                    "updated_at": now,
                }
                if source is None:
                    values.update(
                        status=AQUBPhotoArchiveStatus.DEAD_LETTER.value,
                        available_at=None,
                        last_error_code=error_code or "aqub_photo_source_invalid",
                        last_error_at=now,
                    )
                else:
                    values.update(
                        file_unique_digest=_token_digest(source.photo.file_unique_id),
                        width=source.photo.width,
                        height=source.photo.height,
                        declared_size_bytes=source.photo.file_size,
                        status=AQUBPhotoArchiveStatus.PENDING_DOWNLOAD.value,
                        available_at=now,
                    )
                await session.execute(
                    insert(AQUBPhotoArchive)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["receipt_id"])
                )
        if not rows:
            return None
        last_receipt = rows[-1][0]
        return AQUBPhotoDiscoveryCursor(
            received_at=last_receipt.received_at,
            receipt_id=last_receipt.id,
        )

    async def recover_stale(self, *, observed_at: datetime, limit: int) -> int:
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        if not 1 <= limit <= 100:
            raise ValueError("aqub_photo_recovery_limit_invalid")
        stale_before = now - AQUB_PHOTO_ARCHIVE_STALE_AFTER
        async with self._sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(AQUBPhotoArchive)
                    .where(
                        AQUBPhotoArchive.status
                        == AQUBPhotoArchiveStatus.PROCESSING.value,
                        AQUBPhotoArchive.locked_at <= stale_before,
                    )
                    .order_by(AQUBPhotoArchive.locked_at, AQUBPhotoArchive.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                exhausted = row.attempt_count >= AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS
                row.status = (
                    AQUBPhotoArchiveStatus.DEAD_LETTER.value
                    if exhausted
                    else AQUBPhotoArchiveStatus.RETRY_SCHEDULED.value
                )
                row.available_at = None if exhausted else now
                row.lease_token = None
                row.locked_at = None
                row.last_error_code = "aqub_photo_claim_stale"
                row.last_error_at = now
                row.updated_at = now
        return len(rows)

    async def claim(
        self,
        *,
        not_before: datetime,
        observed_at: datetime,
    ) -> AQUBPhotoArchiveClaim | None:
        cutoff = require_aware_utc(not_before, code="aqub_photo_cutoff_invalid")
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        async with self._sessions.begin() as session:
            archive = await session.scalar(
                select(AQUBPhotoArchive)
                .where(
                    AQUBPhotoArchive.status.in_(
                        (
                            AQUBPhotoArchiveStatus.PENDING_DOWNLOAD.value,
                            AQUBPhotoArchiveStatus.RETRY_SCHEDULED.value,
                        )
                    ),
                    AQUBPhotoArchive.available_at <= now,
                    AQUBPhotoArchive.attempt_count < AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS,
                )
                .order_by(AQUBPhotoArchive.available_at, AQUBPhotoArchive.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if archive is None:
                return None
            source, error_code = await _current_source(
                session,
                archive=archive,
                not_before=cutoff,
                lock=True,
            )
            if source is None or not _source_matches_archive(source, archive):
                archive.status = AQUBPhotoArchiveStatus.DEAD_LETTER.value
                archive.available_at = None
                archive.last_error_code = error_code or "aqub_photo_source_changed"
                archive.last_error_at = now
                archive.updated_at = now
                return None

            lease_token = uuid4()
            attempt = archive.attempt_count + 1
            archive.status = AQUBPhotoArchiveStatus.PROCESSING.value
            archive.attempt_count = attempt
            archive.lease_generation = attempt
            archive.lease_token = lease_token
            archive.locked_at = now
            archive.available_at = None
            archive.last_error_code = None
            archive.last_error_at = None
            archive.updated_at = now
            return AQUBPhotoArchiveClaim(
                archive_id=archive.id,
                receipt_id=archive.receipt_id,
                lease_token=lease_token,
                lease_generation=attempt,
                attempt_count=attempt,
                provider_file_id=source.photo.file_id,
                source_payload_sha256=source.payload_sha256,
                file_unique_digest=_token_digest(source.photo.file_unique_id),
                declared_size_bytes=source.photo.file_size,
            )

    async def complete(
        self,
        *,
        claim: AQUBPhotoArchiveClaim,
        downloaded: DownloadedAQUBPhoto,
        observed_at: datetime,
        not_before: datetime,
    ) -> AQUBPhotoArchiveCompletion:
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        cutoff = require_aware_utc(not_before, code="aqub_photo_cutoff_invalid")
        async with self._sessions.begin() as session:
            archive = await session.scalar(
                select(AQUBPhotoArchive)
                .where(AQUBPhotoArchive.id == claim.archive_id)
                .with_for_update()
            )
            if archive is None or not _claim_owns(archive, claim):
                return AQUBPhotoArchiveCompletion.FENCED
            source, error_code = await _current_source(
                session,
                archive=archive,
                not_before=cutoff,
                lock=True,
            )
            if source is None or not _source_matches_archive(source, archive):
                rejected = await _cas_dead_letter(
                    session,
                    claim=claim,
                    error_code=error_code or "aqub_photo_source_changed",
                    observed_at=now,
                )
                return (
                    AQUBPhotoArchiveCompletion.SOURCE_REJECTED
                    if rejected
                    else AQUBPhotoArchiveCompletion.FENCED
                )
            persisted = await session.scalar(
                update(AQUBPhotoArchive)
                .where(*_claim_predicates(claim))
                .values(
                    status=AQUBPhotoArchiveStatus.ARCHIVED.value,
                    available_at=None,
                    lease_token=None,
                    locked_at=None,
                    raw_data=downloaded.data,
                    raw_sha256=downloaded.sha256,
                    raw_size_bytes=downloaded.size_bytes,
                    raw_media_type=downloaded.media_type,
                    archived_at=func.now(),
                    purge_at=func.now() + func.make_interval(0, 0, 0, 7),
                    raw_purged_at=None,
                    last_error_code=None,
                    last_error_at=None,
                    updated_at=func.now(),
                )
                .returning(AQUBPhotoArchive.id)
            )
            return (
                AQUBPhotoArchiveCompletion.ARCHIVED
                if persisted == claim.archive_id
                else AQUBPhotoArchiveCompletion.FENCED
            )

    async def fail(
        self,
        *,
        claim: AQUBPhotoArchiveClaim,
        failure: AQUBPhotoArchiveError,
        observed_at: datetime,
    ) -> bool:
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        retry = failure.retryable and claim.attempt_count < AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS
        status = (
            AQUBPhotoArchiveStatus.RETRY_SCHEDULED.value
            if retry
            else AQUBPhotoArchiveStatus.DEAD_LETTER.value
        )
        available_at: datetime | None = (
            now + retry_delay(failure, attempt_count=claim.attempt_count)
            if retry
            else None
        )
        async with self._sessions.begin() as session:
            persisted = await session.scalar(
                update(AQUBPhotoArchive)
                .where(*_claim_predicates(claim))
                .values(
                    status=status,
                    lease_token=None,
                    locked_at=None,
                    available_at=available_at,
                    last_error_code=failure.code,
                    last_error_at=now,
                    updated_at=now,
                )
                .returning(AQUBPhotoArchive.id)
            )
            return persisted == claim.archive_id

    async def purge(self, *, observed_at: datetime, limit: int) -> int:
        require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        if not 1 <= limit <= 100:
            raise ValueError("aqub_photo_purge_limit_invalid")
        async with self._sessions.begin() as session:
            due = (
                select(AQUBPhotoArchive.id)
                .where(
                    AQUBPhotoArchive.status
                    == AQUBPhotoArchiveStatus.ARCHIVED.value,
                    AQUBPhotoArchive.purge_at <= func.now(),
                )
                .order_by(AQUBPhotoArchive.purge_at, AQUBPhotoArchive.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .cte("due_aqub_photo_purge")
            )
            purged = (
                await session.scalars(
                    update(AQUBPhotoArchive)
                    .where(AQUBPhotoArchive.id.in_(select(due.c.id)))
                    .values(
                        status=AQUBPhotoArchiveStatus.PURGED.value,
                        raw_data=None,
                        raw_purged_at=func.now(),
                        updated_at=func.now(),
                    )
                    .returning(AQUBPhotoArchive.id)
                )
            ).all()
        return len(purged)


async def _current_source(
    session: AsyncSession,
    *,
    archive: AQUBPhotoArchive,
    not_before: datetime,
    lock: bool,
) -> tuple[_ValidatedSource | None, str | None]:
    statement = (
        select(TelegramReceipt, AQUBInstallationBinding)
        .join(
            AQUBInstallationBinding,
            AQUBInstallationBinding.id == archive.binding_id,
        )
        .where(
            TelegramReceipt.id == archive.receipt_id,
            AQUBInstallationBinding.bot_key == TelegramReceipt.bot_key,
        )
    )
    if lock:
        statement = statement.with_for_update(
            of=(TelegramReceipt, AQUBInstallationBinding)
        )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None, "aqub_photo_binding_missing"
    return _validated_source(
        receipt=row[0],
        binding=row[1],
        not_before=not_before,
    )


def _validated_source(
    *,
    receipt: TelegramReceipt,
    binding: AQUBInstallationBinding,
    not_before: datetime,
) -> tuple[_ValidatedSource | None, str | None]:
    if (
        receipt.bot_key != AQUB_RECEIPT_BOT_KEY
        or binding.bot_key != AQUB_RECEIPT_BOT_KEY
        or receipt.status != TelegramReceiptStatus.PROCESSED.value
        or receipt.processed_at is None
        or receipt.received_at.utcoffset() is None
        or binding.bound_at.utcoffset() is None
        or receipt.received_at.astimezone(UTC) < not_before
    ):
        return None, "aqub_photo_source_scope_invalid"
    payload = dict(receipt.payload)
    inbound = project_aqub_telegram_inbound(
        payload,
        bot_username=AQUB_BOT_USERNAME,
        received_at=receipt.received_at,
    )
    if (
        inbound is None
        or inbound.content_kind is not AQUBTransportContentKind.PHOTO
        or inbound.photo is None
        or inbound.external_id != str(receipt.update_id)
        or inbound.chat_id != binding.group_chat_id
        or inbound.actor_id != binding.alpha_user_id
        or inbound.occurred_at < binding.bound_at
        or inbound.occurred_at.astimezone(UTC) < not_before
        or inbound.received_at < inbound.occurred_at
    ):
        return None, "aqub_photo_source_scope_invalid"
    if (
        inbound.photo.file_size is not None
        and (
            inbound.photo.file_size < 1
            or inbound.photo.file_size > TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES
        )
    ):
        return None, "aqub_photo_declared_size_invalid"
    return (
        _ValidatedSource(
            photo=inbound.photo,
            payload_sha256=canonical_telegram_payload_digest(payload),
        ),
        None,
    )


def _source_matches_archive(
    source: _ValidatedSource,
    archive: AQUBPhotoArchive,
) -> bool:
    return (
        source.payload_sha256 == archive.source_payload_sha256
        and _token_digest(source.photo.file_unique_id) == archive.file_unique_digest
        and source.photo.width == archive.width
        and source.photo.height == archive.height
        and source.photo.file_size == archive.declared_size_bytes
    )


def _claim_owns(archive: AQUBPhotoArchive, claim: AQUBPhotoArchiveClaim) -> bool:
    return (
        archive.status == AQUBPhotoArchiveStatus.PROCESSING.value
        and archive.lease_token == claim.lease_token
        and archive.lease_generation == claim.lease_generation
        and archive.attempt_count == claim.attempt_count
    )


def _claim_predicates(claim: AQUBPhotoArchiveClaim) -> tuple[Any, ...]:
    return (
        AQUBPhotoArchive.id == claim.archive_id,
        AQUBPhotoArchive.status == AQUBPhotoArchiveStatus.PROCESSING.value,
        AQUBPhotoArchive.lease_token == claim.lease_token,
        AQUBPhotoArchive.lease_generation == claim.lease_generation,
        AQUBPhotoArchive.attempt_count == claim.attempt_count,
    )


async def _cas_dead_letter(
    session: AsyncSession,
    *,
    claim: AQUBPhotoArchiveClaim,
    error_code: str,
    observed_at: datetime,
) -> bool:
    persisted = await session.scalar(
        update(AQUBPhotoArchive)
        .where(*_claim_predicates(claim))
        .values(
            status=AQUBPhotoArchiveStatus.DEAD_LETTER.value,
            available_at=None,
            lease_token=None,
            locked_at=None,
            last_error_code=error_code,
            last_error_at=observed_at,
            updated_at=observed_at,
        )
        .returning(AQUBPhotoArchive.id)
    )
    return persisted == claim.archive_id


def _token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
