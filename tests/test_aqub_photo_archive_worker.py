"""Worker lifecycle, fencing, and sticky provider-auth health contract."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from app.application.aqub_photo_archive import (
    AQUBPhotoArchiveClaim,
    AQUBPhotoArchiveCompletion,
    AQUBPhotoArchiveError,
    DownloadedAQUBPhoto,
)
from app.core.config import Settings
from app.worker import aqub_photo_archive as worker

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _claim() -> AQUBPhotoArchiveClaim:
    return AQUBPhotoArchiveClaim(
        archive_id=uuid4(),
        receipt_id=uuid4(),
        lease_token=uuid4(),
        lease_generation=1,
        attempt_count=1,
        provider_file_id="provider-file-id",
        source_payload_sha256="a" * 64,
        file_unique_digest="b" * 64,
        declared_size_bytes=None,
    )


class _Repository:
    def __init__(
        self,
        claims: list[AQUBPhotoArchiveClaim | None],
        *,
        completion: AQUBPhotoArchiveCompletion = AQUBPhotoArchiveCompletion.ARCHIVED,
    ) -> None:
        self.claims = claims
        self.completion = completion
        self.failures: list[AQUBPhotoArchiveError] = []
        self.completions: list[DownloadedAQUBPhoto] = []
        self.purges = 0

    async def recover_stale(self, **_kwargs: Any) -> int:
        return 0

    async def purge(self, **_kwargs: Any) -> int:
        self.purges += 1
        return 0

    async def discover(self, **_kwargs: Any) -> None:
        return None

    async def claim(self, **_kwargs: Any) -> AQUBPhotoArchiveClaim | None:
        return self.claims.pop(0) if self.claims else None

    async def fail(
        self,
        *,
        failure: AQUBPhotoArchiveError,
        **_kwargs: Any,
    ) -> bool:
        self.failures.append(failure)
        return True

    async def complete(
        self,
        *,
        downloaded: DownloadedAQUBPhoto,
        **_kwargs: Any,
    ) -> AQUBPhotoArchiveCompletion:
        self.completions.append(downloaded)
        return self.completion


class _Downloader:
    def __init__(
        self,
        *,
        failure: AQUBPhotoArchiveError | None = None,
    ) -> None:
        self.failure = failure
        self.calls = 0

    async def download(self, **_kwargs: Any) -> DownloadedAQUBPhoto:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        raw = b"\x89PNG\r\n\x1a\narchive"
        return DownloadedAQUBPhoto(
            data=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            media_type="image/png",
        )


class _Health:
    def __init__(self, target: int) -> None:
        self.codes: list[str | None] = []
        self.reached = asyncio.Event()
        self._target = target

    def _record(self, code: str | None) -> None:
        self.codes.append(code)
        if len(self.codes) >= self._target:
            self.reached.set()

    def record_failure(self, _name: str, *, error_code: str, **_kwargs: Any) -> None:
        self._record(error_code)

    def record_success(self, _name: str, **_kwargs: Any) -> None:
        self._record(None)


@pytest.mark.anyio
async def test_provider_auth_is_terminal_and_success_is_completed() -> None:
    auth_repository = _Repository([_claim()])
    auth_downloader = _Downloader(
        failure=AQUBPhotoArchiveError("aqub_photo_provider_auth_rejected")
    )
    auth = await worker.AQUBPhotoArchiveRunner(
        repository=auth_repository,
        downloader=auth_downloader,
        not_before=NOW,
    ).run_once(observed_at=NOW)

    assert auth.sticky_error is True
    assert auth.error_code == "aqub_photo_provider_auth_rejected"
    assert len(auth_repository.failures) == 1
    assert auth_repository.failures[0].retryable is False
    assert auth_repository.completions == []
    assert auth_repository.purges == 0

    success_repository = _Repository([_claim()])
    success_downloader = _Downloader()
    success = await worker.AQUBPhotoArchiveRunner(
        repository=success_repository,
        downloader=success_downloader,
        not_before=NOW,
    ).run_once(observed_at=NOW)

    assert success.provider_succeeded is True
    assert success.error_code is None
    assert len(success_repository.completions) == 1

    rejected_repository = _Repository(
        [_claim()],
        completion=AQUBPhotoArchiveCompletion.SOURCE_REJECTED,
    )
    rejected = await worker.AQUBPhotoArchiveRunner(
        repository=rejected_repository,
        downloader=_Downloader(),
        not_before=NOW,
    ).run_once(observed_at=NOW)
    assert rejected.provider_succeeded is True
    assert rejected.error_code == "aqub_photo_source_rejected"


@pytest.mark.anyio
async def test_auth_degradation_is_sticky_across_idle_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository([_claim(), None, None])
    downloader = _Downloader(
        failure=AQUBPhotoArchiveError("aqub_photo_provider_auth_rejected")
    )
    health = _Health(target=3)
    supplied_client: list[httpx.AsyncClient] = []
    monkeypatch.setattr(worker, "SqlAQUBPhotoArchiveRepository", lambda _sessions: repository)

    def downloader_factory(
        *,
        bot_token: str,
        http_client: httpx.AsyncClient,
    ) -> _Downloader:
        assert bot_token == "bot-token"
        supplied_client.append(http_client)
        return downloader

    monkeypatch.setattr(worker, "TelegramAQUBPhotoDownloader", downloader_factory)
    settings = Settings(
        _env_file=None,
        GATE_AQUB_PHOTO_ARCHIVE_ENABLED=True,
        GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=True,
        GATE_AQUB_PHOTO_ARCHIVE_NOT_BEFORE=NOW,
        TELEGRAM_AQUB_BOT_TOKEN="bot-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        trust_env=False,
    ) as shared:
        task = asyncio.create_task(
            worker.run_aqub_photo_archive_worker_loop(
                settings=settings,
                session_factory=object(),  # type: ignore[arg-type]
                http_client=shared,
                health_reporter=health,
                poll_interval_seconds=0.001,
            )
        )
        await asyncio.wait_for(health.reached.wait(), timeout=1)
        assert task.done() is False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert supplied_client == [shared]
    assert health.codes[:3] == [
        "aqub_photo_provider_auth_rejected",
        "aqub_photo_provider_auth_rejected",
        "aqub_photo_provider_auth_rejected",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("settings", "expected_code"),
    [
        (
            Settings(
                _env_file=None,
                GATE_AQUB_PHOTO_ARCHIVE_ENABLED=True,
                GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=True,
                GATE_AQUB_PHOTO_ARCHIVE_NOT_BEFORE=NOW,
            ),
            "aqub_photo_bot_token_required",
        ),
        (
            Settings(
                _env_file=None,
                GATE_AQUB_PHOTO_ARCHIVE_ENABLED=True,
                GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=True,
                TELEGRAM_AQUB_BOT_TOKEN="bot-token",
            ),
            "aqub_photo_cutoff_required",
        ),
    ],
)
async def test_invalid_enabled_initialization_stays_live_and_degraded(
    settings: Settings,
    expected_code: str,
) -> None:
    health = _Health(target=2)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        trust_env=False,
    ) as shared:
        task = asyncio.create_task(
            worker.run_aqub_photo_archive_worker_loop(
                settings=settings,
                session_factory=object(),  # type: ignore[arg-type]
                http_client=shared,
                health_reporter=health,
                poll_interval_seconds=0.001,
            )
        )
        await asyncio.wait_for(health.reached.wait(), timeout=1)
        assert task.done() is False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert health.codes == [expected_code, expected_code]


def test_archive_is_default_off_and_requires_exact_utc_cutoff() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.gate_aqub_photo_archive_enabled is False
    assert defaults.gate_aqub_photo_archive_purge_enabled is False
    blank_env_default = Settings(
        _env_file=None,
        GATE_AQUB_PHOTO_ARCHIVE_ENABLED=False,
        GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=False,
        GATE_AQUB_PHOTO_ARCHIVE_NOT_BEFORE="   ",
    )
    assert blank_env_default.gate_aqub_photo_archive_not_before is None
    with pytest.raises(AQUBPhotoArchiveError, match="aqub_photo_cutoff_invalid"):
        worker.AQUBPhotoArchiveRunner(
            repository=_Repository([]),
            downloader=_Downloader(),
            not_before=datetime.fromisoformat("2026-08-20T14:00:00+02:00"),
        )


@pytest.mark.anyio
async def test_archive_enabled_without_purge_degrades_before_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = _Health(target=2)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("repository/downloader must not be constructed")

    monkeypatch.setattr(worker, "SqlAQUBPhotoArchiveRepository", forbidden)
    monkeypatch.setattr(worker, "TelegramAQUBPhotoDownloader", forbidden)
    settings = Settings(
        _env_file=None,
        GATE_AQUB_PHOTO_ARCHIVE_ENABLED=True,
        GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=False,
        GATE_AQUB_PHOTO_ARCHIVE_NOT_BEFORE=NOW,
        TELEGRAM_AQUB_BOT_TOKEN="bot-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        trust_env=False,
    ) as shared:
        task = asyncio.create_task(
            worker.run_aqub_photo_archive_worker_loop(
                settings=settings,
                session_factory=object(),  # type: ignore[arg-type]
                http_client=shared,
                health_reporter=health,
                poll_interval_seconds=0.001,
            )
        )
        await asyncio.wait_for(health.reached.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert health.codes == ["aqub_photo_purge_required"] * 2


@pytest.mark.anyio
async def test_purge_only_loop_needs_no_settings_token_cutoff_or_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository([])
    health = _Health(target=2)
    constructed_with: list[object] = []

    def repository_factory(session_factory: object) -> _Repository:
        constructed_with.append(session_factory)
        return repository

    monkeypatch.setattr(worker, "SqlAQUBPhotoArchiveRepository", repository_factory)
    session_factory = object()
    task = asyncio.create_task(
        worker.run_aqub_photo_archive_purge_worker_loop(
            session_factory=session_factory,  # type: ignore[arg-type]
            health_reporter=health,
            poll_interval_seconds=0.001,
        )
    )
    await asyncio.wait_for(health.reached.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert constructed_with == [session_factory]
    assert repository.purges == 2
    assert health.codes == [None, None]
