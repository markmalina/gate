"""Drain durable Watch archive jobs without running transcription or GitHub I/O."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_early_cards import watch_scope
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.infrastructure.watch_voice_recovery import _utc
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor
from app.worker.dropbox_calls import (
    DropboxCallWorkerError,
    _refresh_access_token,
    _verify_account_id,
)
from app.worker.watch_voice import source_entry, store_entry

logger = logging.getLogger(__name__)
BATCH_SIZE = 5
ARCHIVE_LOCK = 0x57415444
RECOVERY_SECONDS = 60
RETRY_DELAY = timedelta(minutes=5)


async def archive_watch_pass(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> bool:
    scope = watch_scope(settings)
    workspace, account = scope["workspace_key"], scope["account_id"]
    async with factory() as lock:
        if not await lock.scalar(select(func.pg_try_advisory_xact_lock(ARCHIVE_LOCK))):
            return False
        cursor = await lock.get(WatchEarlyCursor, (workspace, account))
        if cursor is None or cursor.writer_mode != "app_webhook":
            return False
        cards = (await lock.scalars(
            select(WatchEarlyCard)
            .where(
                WatchEarlyCard.workspace_key == workspace,
                WatchEarlyCard.account_id == account,
                WatchEarlyCard.archive_input.is_not(None),
                or_(
                    WatchEarlyCard.archive_retry_at.is_(None),
                    WatchEarlyCard.archive_retry_at <= datetime.now(UTC),
                ),
            )
            .order_by(WatchEarlyCard.detected_at, WatchEarlyCard.id)
            .limit(BATCH_SIZE)
        )).all()
        if not cards:
            return False
        token = await _refresh_access_token(settings, http)
        await _verify_account_id(settings, http, token)
        for card in cards:
            changes: dict[str, object]
            try:
                item = source_entry({
                    **(card.archive_input or {}),
                    ".tag": "file",
                    "id": card.file_id,
                    "rev": card.revision,
                    "server_modified": _utc(card.source_modified_at).isoformat(),
                })
                if item is None:
                    raise WatchVoiceError("watch_archive_input_invalid")
                async with asyncio.timeout(80):
                    await store_entry(settings, factory, http, token, item, webhook_archived=True)
            except (DropboxCallWorkerError, WatchVoiceError, TimeoutError):
                changes = {"archive_retry_at": datetime.now(UTC) + RETRY_DELAY}
                logger.warning("Watch archive deferred")
            else:
                changes = {"archive_input": None, "archive_retry_at": None}
            async with factory.begin() as session:
                await session.execute(
                    update(WatchEarlyCard).where(WatchEarlyCard.id == card.id).values(**changes)
                )
        return len(cards) == BATCH_SIZE


async def run_watch_archive_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
) -> None:
    if (
        not settings.gate_watch_webhook_enabled
        or not settings.gate_watch_webhook_archive_enabled
        or settings.gate_workspace_id != "mark-personal"
        or settings.gate_watch_voice_enabled
        or not settings.dropbox_call_account_id
    ):
        return
    async with httpx.AsyncClient(timeout=12, trust_env=False) as http:
        while True:
            wake.clear()
            try:
                if await archive_watch_pass(settings, factory, http):
                    continue
            except Exception:
                logger.warning("Watch archive pass deferred")
                await asyncio.sleep(RECOVERY_SECONDS)
                continue
            try:
                await asyncio.wait_for(wake.wait(), RECOVERY_SECONDS)
            except TimeoutError:
                pass
