"""Bounded contracts for asynchronous AQUB owner-photo byte archival."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES = 20_000_000
AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS = 5
AQUB_PHOTO_ARCHIVE_STALE_AFTER = timedelta(minutes=5)
AQUB_PHOTO_ARCHIVE_RETENTION = timedelta(days=7)


class AQUBPhotoArchiveStatus(StrEnum):
    PENDING_DOWNLOAD = "pending_download"
    PROCESSING = "processing"
    RETRY_SCHEDULED = "retry_scheduled"
    ARCHIVED = "archived"
    PURGED = "purged"
    DEAD_LETTER = "dead_letter"


class AQUBPhotoArchiveCompletion(StrEnum):
    ARCHIVED = "archived"
    SOURCE_REJECTED = "source_rejected"
    FENCED = "fenced"


class AQUBPhotoArchiveError(RuntimeError):
    """A sanitized provider or archive failure, safe to persist as a code."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None:
            raise ValueError("aqub_photo_error_code_invalid")
        if retry_after_seconds is not None and not retryable:
            raise ValueError("aqub_photo_retry_after_requires_retryable")
        if retry_after_seconds is not None and not 1 <= retry_after_seconds <= 86_400:
            raise ValueError("aqub_photo_retry_after_invalid")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True, repr=False)
class AQUBPhotoArchiveClaim:
    archive_id: UUID
    receipt_id: UUID
    lease_token: UUID = field(repr=False)
    lease_generation: int
    attempt_count: int
    provider_file_id: str = field(repr=False)
    source_payload_sha256: str
    file_unique_digest: str
    declared_size_bytes: int | None

    def __post_init__(self) -> None:
        if (
            type(self.attempt_count) is not int
            or not 1 <= self.attempt_count <= AQUB_PHOTO_ARCHIVE_MAX_ATTEMPTS
        ):
            raise ValueError("aqub_photo_attempt_invalid")
        if (
            type(self.lease_generation) is not int
            or self.lease_generation != self.attempt_count
        ):
            raise ValueError("aqub_photo_lease_generation_invalid")
        if (
            not isinstance(self.provider_file_id, str)
            or not self.provider_file_id
            or self.provider_file_id != self.provider_file_id.strip()
            or len(self.provider_file_id) > 512
        ):
            raise ValueError("aqub_photo_file_id_invalid")
        if (
            not isinstance(self.source_payload_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_payload_sha256) is None
            or not isinstance(self.file_unique_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.file_unique_digest) is None
        ):
            raise ValueError("aqub_photo_claim_digest_invalid")
        if self.declared_size_bytes is not None and (
            type(self.declared_size_bytes) is not int
            or not 1
            <= self.declared_size_bytes
            <= TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES
        ):
            raise ValueError("aqub_photo_declared_size_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DownloadedAQUBPhoto:
    data: bytes = field(repr=False)
    sha256: str
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        if (
            type(self.data) is not bytes
            or not self.data
            or self.size_bytes != len(self.data)
            or self.size_bytes > TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES
            or not isinstance(self.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
            or hashlib.sha256(self.data).hexdigest() != self.sha256
            or self.media_type not in {"image/jpeg", "image/png", "image/webp"}
        ):
            raise ValueError("aqub_photo_download_invalid")


@dataclass(frozen=True, slots=True)
class AQUBPhotoDiscoveryCursor:
    received_at: datetime
    receipt_id: UUID


class AQUBPhotoArchiveRepository(Protocol):
    async def discover(
        self,
        *,
        not_before: datetime,
        cursor: AQUBPhotoDiscoveryCursor | None,
        limit: int,
        observed_at: datetime,
    ) -> AQUBPhotoDiscoveryCursor | None: ...

    async def recover_stale(self, *, observed_at: datetime, limit: int) -> int: ...

    async def claim(
        self,
        *,
        not_before: datetime,
        observed_at: datetime,
    ) -> AQUBPhotoArchiveClaim | None: ...

    async def complete(
        self,
        *,
        claim: AQUBPhotoArchiveClaim,
        downloaded: DownloadedAQUBPhoto,
        observed_at: datetime,
        not_before: datetime,
    ) -> AQUBPhotoArchiveCompletion: ...

    async def fail(
        self,
        *,
        claim: AQUBPhotoArchiveClaim,
        failure: AQUBPhotoArchiveError,
        observed_at: datetime,
    ) -> bool: ...

    async def purge(self, *, observed_at: datetime, limit: int) -> int: ...


class AQUBPhotoDownloader(Protocol):
    async def download(self, *, claim: AQUBPhotoArchiveClaim) -> DownloadedAQUBPhoto: ...


def require_aware_utc(value: datetime | None, *, code: str) -> datetime:
    if value is None or value.utcoffset() != timedelta(0):
        raise AQUBPhotoArchiveError(code)
    return value.astimezone(UTC)


def retry_delay(failure: AQUBPhotoArchiveError, *, attempt_count: int) -> timedelta:
    if failure.retry_after_seconds is not None:
        return timedelta(seconds=failure.retry_after_seconds)
    seconds = min(300, 5 * (2 ** max(0, attempt_count - 1)))
    return timedelta(seconds=seconds)
