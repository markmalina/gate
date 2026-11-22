"""Wake the existing metadata publisher after a committed Dropbox webhook."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_app_auth import WatchAppInstallation, issue_mark_token
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_early_cards import (
    discover_early_cards,
    drain_saved,
    watch_scope,
)
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor
from app.worker.watch_app_runner import app_installation

logger = logging.getLogger(__name__)
RECOVERY_SECONDS = 60
BATCH_SIZE = 5
# Dedicated PostgreSQL advisory-lock namespace for the one Mark Watch route.
WRITER_LOCK = 0x57415443


async def run_watch_pass(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    key: str,
    installation: WatchAppInstallation,
    archive_wake: asyncio.Event | None = None,
) -> bool:
    """Return whether the bounded pass left work that should run immediately."""
    scope = watch_scope(settings)
    pk = (scope["workspace_key"], scope["account_id"])
    async with factory() as lock:
        if not await lock.scalar(select(func.pg_try_advisory_xact_lock(WRITER_LOCK))):
            return False
        cursor = await lock.get(WatchEarlyCursor, pk)
        if cursor is None or cursor.writer_mode != "app_webhook":
            return False
        generation = cursor.wake_generation
        trigger = cursor.wake_received_at if generation > cursor.wake_processed_generation else None
        more_pages = False
        discovery_error: WatchVoiceError | None = None
        try:
            more_pages = await discover_early_cards(
                settings, factory, http, writer="app_webhook", wake_received_at=trigger
            )
        except WatchVoiceError as exc:
            discovery_error = exc
        if archive_wake is not None:
            archive_wake.set()

        if trigger is not None and not more_pages and discovery_error is None:
            async with factory.begin() as session:
                await session.execute(
                    update(WatchEarlyCursor)
                    .where(
                        WatchEarlyCursor.workspace_key == pk[0],
                        WatchEarlyCursor.account_id == pk[1],
                        WatchEarlyCursor.writer_mode == "app_webhook",
                        WatchEarlyCursor.writer_generation == cursor.writer_generation,
                        WatchEarlyCursor.wake_processed_generation < generation,
                    )
                    .values(wake_processed_generation=generation)
                )

        async with factory() as session:
            has_cards = await session.scalar(
                select(WatchEarlyCard.id)
                .where(
                    WatchEarlyCard.workspace_key == pk[0],
                    WatchEarlyCard.account_id == pk[1],
                    WatchEarlyCard.state.in_(("pending", "sending", "unknown")),
                )
                .limit(1)
            )
        immediate = False
        if has_cards:
            async with asyncio.timeout(95):
                token = await issue_mark_token(http, key, installation)
                client = AppInboxClient(http, token, app_slug=installation.slug)
                await client.preflight()
                receipt = await drain_saved(
                    factory, client, scope, BATCH_SIZE, writer="app_webhook"
                )
            if receipt["considered"]:
                async with factory() as session:
                    immediate = bool(
                        await session.scalar(
                            select(WatchEarlyCard.id)
                            .where(
                                WatchEarlyCard.workspace_key == pk[0],
                                WatchEarlyCard.account_id == pk[1],
                                WatchEarlyCard.state == "pending",
                            )
                            .limit(1)
                        )
                    )
        if discovery_error is not None:
            logger.warning("Watch delta unavailable")
        async with factory() as session:
            newer_wake = await session.scalar(
                select(WatchEarlyCursor.wake_generation).where(
                    WatchEarlyCursor.workspace_key == pk[0],
                    WatchEarlyCursor.account_id == pk[1],
                    WatchEarlyCursor.writer_mode == "app_webhook",
                    WatchEarlyCursor.wake_generation > generation,
                )
            )
        return more_pages or immediate or newer_wake is not None


async def run_watch_webhook_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
    archive_wake: asyncio.Event | None = None,
    transcription_wake: asyncio.Event | None = None,
) -> None:
    key = os.getenv("GATE_WATCH_APP_PRIVATE_KEY", "")
    installation = app_installation()
    if (
        not key
        or installation is None
        or settings.gate_workspace_id != "mark-personal"
        or settings.gate_watch_voice_enabled
        or not settings.dropbox_call_account_id
    ):
        logger.error("Watch webhook writer configuration invalid")
        return

    async with httpx.AsyncClient(timeout=12.0, trust_env=False) as http:
        while True:
            # Clear before the scan so a signal concurrent with it remains visible.
            wake.clear()
            try:
                more = await run_watch_pass(
                    settings, factory, http, key, installation, archive_wake
                )
                if transcription_wake is not None:
                    transcription_wake.set()
                if more:
                    continue
            except Exception:
                # Error details may include a provider URL or credential.
                logger.warning("Watch webhook writer pass failed")
                # A broken provider must not be hammered by repeated webhooks.
                await asyncio.sleep(RECOVERY_SECONDS)
                continue
            try:
                await asyncio.wait_for(wake.wait(), RECOVERY_SECONDS)
            except TimeoutError:
                pass
