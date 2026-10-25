"""Durable, bounded discovery/download queue; no transcription or publication."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_journal import pulse, safe_code
from app.models.watch_voice import WatchVoiceSource


def source_id(settings: Settings, file_id: str, revision: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            repr(
                (
                    settings.gate_workspace_id,
                    settings.dropbox_call_account_id,
                    file_id,
                    revision,
                )
            ),
        )
    )


def source_scope(settings: Settings, cutover: datetime | None = None) -> tuple[Any, ...]:
    scope: tuple[Any, ...] = (
        WatchVoiceSource.workspace_key == settings.gate_workspace_id,
        WatchVoiceSource.account_id == settings.dropbox_call_account_id,
    )
    if settings.gate_watch_voice_file_id:
        scope += (
            WatchVoiceSource.file_id == settings.gate_watch_voice_file_id,
            WatchVoiceSource.revision == settings.gate_watch_voice_revision,
        )
    if cutover is not None:
        scope += (
            WatchVoiceSource.dropbox_modified_at > cutover,
            WatchVoiceSource.detected_at >= cutover,
        )
    return scope


async def discover(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    item: Any,
    cutover: datetime | None,
) -> str | None:
    from app.infrastructure.watch_voice_github import WatchVoiceError
    from app.worker.watch_voice import PATH, source_entry, timestamp

    if not isinstance(item, dict) or item.get(".tag") != "file":
        return None
    file_id, revision = item.get("id"), item.get("rev")
    if settings.gate_watch_voice_file_id and (
        file_id != settings.gate_watch_voice_file_id
        or revision != settings.gate_watch_voice_revision
    ):
        return None
    path, name = item.get("path_display"), item.get("name")
    if (
        not isinstance(path, str)
        or not path.casefold().startswith(PATH.casefold() + "/")
        or not isinstance(name, str)
        or PurePosixPath(name).suffix.lower()
        not in {
            ".m4a",
            ".mp4",
            ".mp3",
            ".wav",
            ".ogg",
            ".webm",
            ".flac",
        }
    ):
        return None
    if any(
        not isinstance(value, str) or not value or len(value) > 255
        for value in (file_id, revision, item.get("server_modified"))
    ):
        raise WatchVoiceError("watch_voice_source_metadata_invalid")
    modified = timestamp(item["server_modified"])
    if cutover is not None and modified <= cutover:
        return None
    error = None
    try:
        source_entry(item)
    except WatchVoiceError as exc:
        if exc.code not in {"watch_voice_source_size_limit", "watch_voice_source_name_limit"}:
            raise
        error = exc.code
    assert isinstance(file_id, str) and isinstance(revision, str)
    key = source_id(settings, file_id, revision)
    async with factory() as session:
        if await session.get(WatchVoiceSource, key) is not None:
            return key
        row = WatchVoiceSource(
            id=key,
            workspace_key=settings.gate_workspace_id,
            account_id=settings.dropbox_call_account_id,
            file_id=file_id,
            revision=revision,
            filename=name[:255],
            source_path=path[:1024],
            size_bytes=item["size"]
            if type(item.get("size")) is int and 0 <= item["size"] <= 2**63 - 1
            else None,
            dropbox_modified_at=modified,
            detected_at=datetime.now(UTC),
            state="rejected" if error else "pending",
            attempt_count=0,
            last_error=error,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            if await session.get(WatchVoiceSource, key) is None:
                raise
    return key


async def archive_source(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    token: str,
    key: str,
) -> None:
    from app.infrastructure.watch_voice_github import WatchVoiceError
    from app.worker.dropbox_calls import DropboxCallWorkerError
    from app.worker.watch_voice import store_entry

    now, claim = datetime.now(UTC), str(uuid.uuid4())
    async with factory() as session:
        selected = await session.execute(
            update(WatchVoiceSource)
            .where(
                WatchVoiceSource.id == key,
                *source_scope(settings),
                or_(
                    WatchVoiceSource.state.in_(("pending", "retry")),
                    and_(
                        WatchVoiceSource.state == "downloading",
                        WatchVoiceSource.lease_expires_at < now,
                    ),
                ),
                or_(
                    WatchVoiceSource.next_attempt_at.is_(None),
                    WatchVoiceSource.next_attempt_at <= now,
                ),
            )
            .values(
                state="downloading",
                claim_id=claim,
                lease_expires_at=now + timedelta(seconds=120),
                attempt_count=WatchVoiceSource.attempt_count + 1,
            )
        )
        await session.commit()
        if not selected.rowcount:  # type: ignore[attr-defined]
            return
        row = await session.get(WatchVoiceSource, key)
        assert row is not None and row.dropbox_modified_at is not None
    await pulse("source_download", seconds=110)
    code, state = None, "archived"
    try:
        async with asyncio.timeout(100):
            modified = row.dropbox_modified_at
            modified = (
                modified.replace(tzinfo=UTC)
                if modified.tzinfo is None
                else (modified.astimezone(UTC))
            )
            await store_entry(
                settings,
                factory,
                http,
                token,
                {
                    "id": row.file_id,
                    "rev": row.revision,
                    "name": row.filename,
                    "path_display": row.source_path,
                    "size": row.size_bytes,
                    "server_modified": modified.isoformat(),
                },
                detected_at=row.detected_at,
            )
    except (DropboxCallWorkerError, WatchVoiceError, TimeoutError) as exc:
        code = safe_code(getattr(exc, "code", "watch_voice_download_timeout"))
        if code in {"watch_voice_source_conflict", "watch_voice_archive_integrity_failed"}:
            raise
        state = (
            "rejected"
            if code
            in {
                "dropbox_call_download_limit_exceeded",
                "dropbox_call_download_size_mismatch",
            }
            else "retry"
        )
    retry_at = datetime.now(UTC) + timedelta(
        seconds=min(300, 30 * 2 ** min(row.attempt_count - 1, 4))
    )
    async with factory() as session:
        await session.execute(
            update(WatchVoiceSource)
            .where(
                WatchVoiceSource.id == key,
                WatchVoiceSource.claim_id == claim,
                WatchVoiceSource.state == "downloading",
            )
            .values(
                state=state,
                last_error=code,
                claim_id=None,
                lease_expires_at=None,
                next_attempt_at=retry_at if state == "retry" else None,
                archived_at=datetime.now(UTC) if state == "archived" else None,
            )
        )
        await session.commit()


async def drain_sources(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    token: str,
    cutover: datetime | None = None,
) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        keys = list(
            (
                await session.scalars(
                    select(WatchVoiceSource.id)
                    .where(
                        *source_scope(settings, cutover),
                        or_(
                            WatchVoiceSource.state.in_(("pending", "retry")),
                            and_(
                                WatchVoiceSource.state == "downloading",
                                WatchVoiceSource.lease_expires_at < now,
                            ),
                        ),
                        or_(
                            WatchVoiceSource.next_attempt_at.is_(None),
                            WatchVoiceSource.next_attempt_at <= now,
                        ),
                    )
                    .order_by(WatchVoiceSource.detected_at, WatchVoiceSource.id)
                    .limit(50)
                )
            ).all()
        )
    for key in keys:
        await archive_source(settings, factory, http, token, key)
