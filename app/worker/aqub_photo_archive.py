"""Isolated background loop for default-off AQUB photo byte archival."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.aqub_photo_archive import (
    AQUBPhotoArchiveCompletion,
    AQUBPhotoArchiveError,
    AQUBPhotoArchiveRepository,
    AQUBPhotoDiscoveryCursor,
    AQUBPhotoDownloader,
    require_aware_utc,
)
from app.application.worker_health import (
    AQUB_PHOTO_ARCHIVE_PURGE_WORKER,
    AQUB_PHOTO_ARCHIVE_WORKER,
    WorkerHealthReporter,
)
from app.core.config import Settings
from app.infrastructure.aqub_photo_archive import SqlAQUBPhotoArchiveRepository
from app.infrastructure.aqub_photo_downloader import TelegramAQUBPhotoDownloader


@dataclass(frozen=True, slots=True)
class AQUBPhotoArchiveIteration:
    provider_succeeded: bool = False
    error_code: str | None = None
    sticky_error: bool = False


class AQUBPhotoArchiveRunner:
    """Run one bounded discovery/claim/download iteration at a time."""

    def __init__(
        self,
        *,
        repository: AQUBPhotoArchiveRepository,
        downloader: AQUBPhotoDownloader,
        not_before: datetime,
    ) -> None:
        self._repository = repository
        self._downloader = downloader
        self._not_before = require_aware_utc(
            not_before,
            code="aqub_photo_cutoff_invalid",
        )
        self._cursor: AQUBPhotoDiscoveryCursor | None = None

    async def run_once(self, *, observed_at: datetime) -> AQUBPhotoArchiveIteration:
        now = require_aware_utc(observed_at, code="aqub_photo_timestamp_invalid")
        await self._repository.recover_stale(observed_at=now, limit=100)
        self._cursor = await self._repository.discover(
            not_before=self._not_before,
            cursor=self._cursor,
            limit=100,
            observed_at=now,
        )
        claim = await self._repository.claim(
            not_before=self._not_before,
            observed_at=now,
        )
        if claim is None:
            return AQUBPhotoArchiveIteration()
        try:
            downloaded = await self._downloader.download(claim=claim)
        except AQUBPhotoArchiveError as failure:
            await self._repository.fail(
                claim=claim,
                failure=failure,
                observed_at=datetime.now(UTC),
            )
            return AQUBPhotoArchiveIteration(
                error_code=failure.code,
                sticky_error=failure.code == "aqub_photo_provider_auth_rejected",
            )
        except Exception:
            await self._repository.fail(
                claim=claim,
                failure=AQUBPhotoArchiveError(
                    "aqub_photo_download_failed",
                    retryable=True,
                ),
                observed_at=datetime.now(UTC),
            )
            return AQUBPhotoArchiveIteration(
                error_code="aqub_photo_download_failed"
            )
        completion = await self._repository.complete(
            claim=claim,
            downloaded=downloaded,
            observed_at=datetime.now(UTC),
            not_before=self._not_before,
        )
        return AQUBPhotoArchiveIteration(
            provider_succeeded=True,
            error_code=(
                None
                if completion is AQUBPhotoArchiveCompletion.ARCHIVED
                else "aqub_photo_source_rejected"
                if completion is AQUBPhotoArchiveCompletion.SOURCE_REJECTED
                else "aqub_photo_claim_fenced"
            ),
        )


async def run_aqub_photo_archive_worker_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    health_reporter: WorkerHealthReporter,
    poll_interval_seconds: float = 1.0,
) -> None:
    """Construct provider resources only after the default-off gate is enabled."""

    runner: AQUBPhotoArchiveRunner | None = None
    sticky_error_code: str | None = None
    sleep_seconds = poll_interval_seconds if poll_interval_seconds > 0 else 1.0
    while True:
        observed_at = datetime.now(UTC)
        try:
            if poll_interval_seconds <= 0:
                raise AQUBPhotoArchiveError("aqub_photo_poll_interval_invalid")
            if not settings.gate_aqub_photo_archive_enabled:
                raise AQUBPhotoArchiveError("aqub_photo_worker_disabled")
            if not settings.gate_aqub_photo_archive_purge_enabled:
                raise AQUBPhotoArchiveError("aqub_photo_purge_required")
            if runner is None:
                not_before = require_aware_utc(
                    settings.gate_aqub_photo_archive_not_before,
                    code="aqub_photo_cutoff_required",
                )
                bot_token = settings.telegram_aqub_bot_token.get_secret_value().strip()
                if not bot_token:
                    raise AQUBPhotoArchiveError("aqub_photo_bot_token_required")
                runner = AQUBPhotoArchiveRunner(
                    repository=SqlAQUBPhotoArchiveRepository(session_factory),
                    downloader=TelegramAQUBPhotoDownloader(
                        bot_token=bot_token,
                        http_client=http_client,
                    ),
                    not_before=not_before,
                )
            result = await runner.run_once(observed_at=observed_at)
        except AQUBPhotoArchiveError as exc:
            health_reporter.record_failure(
                AQUB_PHOTO_ARCHIVE_WORKER,
                error_code=exc.code,
                observed_at=observed_at,
            )
        except Exception:
            health_reporter.record_failure(
                AQUB_PHOTO_ARCHIVE_WORKER,
                error_code="aqub_photo_worker_iteration_failed",
                observed_at=observed_at,
            )
        else:
            if result.provider_succeeded:
                sticky_error_code = None
            if result.sticky_error and result.error_code is not None:
                sticky_error_code = result.error_code
            error_code = result.error_code or sticky_error_code
            if error_code is not None:
                health_reporter.record_failure(
                    AQUB_PHOTO_ARCHIVE_WORKER,
                    error_code=error_code,
                    observed_at=observed_at,
                )
            else:
                health_reporter.record_success(
                    AQUB_PHOTO_ARCHIVE_WORKER,
                    observed_at=observed_at,
                )
        await asyncio.sleep(sleep_seconds)


async def run_aqub_photo_archive_purge_worker_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    health_reporter: WorkerHealthReporter,
    poll_interval_seconds: float = 1.0,
) -> None:
    """Purge due BYTEA independently of discovery and Telegram provider access."""

    repository: SqlAQUBPhotoArchiveRepository | None = None
    sleep_seconds = poll_interval_seconds if poll_interval_seconds > 0 else 1.0
    while True:
        observed_at = datetime.now(UTC)
        try:
            if poll_interval_seconds <= 0:
                raise AQUBPhotoArchiveError("aqub_photo_purge_poll_interval_invalid")
            if repository is None:
                repository = SqlAQUBPhotoArchiveRepository(session_factory)
            await repository.purge(observed_at=observed_at, limit=100)
        except AQUBPhotoArchiveError as exc:
            health_reporter.record_failure(
                AQUB_PHOTO_ARCHIVE_PURGE_WORKER,
                error_code=exc.code,
                observed_at=observed_at,
            )
        except Exception:
            health_reporter.record_failure(
                AQUB_PHOTO_ARCHIVE_PURGE_WORKER,
                error_code="aqub_photo_purge_iteration_failed",
                observed_at=observed_at,
            )
        else:
            health_reporter.record_success(
                AQUB_PHOTO_ARCHIVE_PURGE_WORKER,
                observed_at=observed_at,
            )
        await asyncio.sleep(sleep_seconds)
