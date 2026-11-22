"""Drive Watch Dropbox discovery and Mark delivery independently."""

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
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor
from app.worker.watch_app_runner import app_installation

logger = logging.getLogger(__name__)
RECOVERY_SECONDS = 60
BATCH_SIZE = 5
WRITER_LOCK = 0x57415443
DISCOVERY_LOCK = 0x57415446


async def discover_watch_pass(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    archive_wake: asyncio.Event | None,
    mark_wake: asyncio.Event,
) -> bool:
    """Advance the Dropbox cursor without contacting GitHub."""
    scope = watch_scope(settings)
    pk = (scope["workspace_key"], scope["account_id"])
    async with factory() as lock:
        await lock.execute(select(func.pg_advisory_xact_lock(DISCOVERY_LOCK)))
        cursor = await lock.get(WatchEarlyCursor, pk)
        if cursor is None or cursor.writer_mode != "app_webhook":
            return False
        generation = cursor.wake_generation
        trigger = cursor.wake_received_at if generation > cursor.wake_processed_generation else None
        more = await discover_early_cards(
            settings, factory, http, writer="app_webhook", wake_received_at=trigger
        )
        if archive_wake is not None:
            archive_wake.set()
        mark_wake.set()

        if trigger is not None and not more:
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
        return more


async def publish_watch_pass(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    key: str,
    installation: WatchAppInstallation,
) -> bool:
    """Keep the existing single-writer contract for early Mark cards."""
    scope = watch_scope(settings)
    pk = (scope["workspace_key"], scope["account_id"])
    async with factory() as lock:
        if not await lock.scalar(select(func.pg_try_advisory_xact_lock(WRITER_LOCK))):
            return False
        cursor = await lock.get(WatchEarlyCursor, pk)
        if cursor is None or cursor.writer_mode != "app_webhook":
            return False
        has_cards = await lock.scalar(
            select(WatchEarlyCard.id)
            .where(
                WatchEarlyCard.workspace_key == pk[0],
                WatchEarlyCard.account_id == pk[1],
                WatchEarlyCard.state.in_(("pending", "sending", "unknown")),
            )
            .limit(1)
        )
        if not has_cards:
            return False
        async with asyncio.timeout(95):
            token = await issue_mark_token(http, key, installation)
            client = AppInboxClient(http, token, app_slug=installation.slug)
            await client.preflight()
            receipt = await drain_saved(factory, client, scope, BATCH_SIZE, writer="app_webhook")
        if not receipt["considered"]:
            return False
        async with factory() as session:
            return bool(await session.scalar(
                select(WatchEarlyCard.id)
                .where(
                    WatchEarlyCard.workspace_key == pk[0],
                    WatchEarlyCard.account_id == pk[1],
                    WatchEarlyCard.state == "pending",
                )
                .limit(1)
            ))


async def run_watch_webhook_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
    archive_wake: asyncio.Event | None,
    mark_wake: asyncio.Event,
) -> None:
    if (
        settings.gate_workspace_id != "mark-personal"
        or settings.gate_watch_voice_enabled
        or not settings.dropbox_call_account_id
    ):
        logger.warning("Watch Dropbox discovery configuration invalid")
        return
    async with httpx.AsyncClient(timeout=12, trust_env=False) as http:
        while True:
            wake.clear()
            try:
                if await discover_watch_pass(settings, factory, http, archive_wake, mark_wake):
                    continue
            except Exception:
                logger.warning("Watch Dropbox discovery deferred")
                await asyncio.sleep(RECOVERY_SECONDS)
                continue
            await wake.wait()


async def run_watch_mark_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
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
        logger.warning("Watch Mark writer configuration invalid")
        return
    async with httpx.AsyncClient(timeout=12, trust_env=False) as http:
        while True:
            wake.clear()
            try:
                if await publish_watch_pass(settings, factory, http, key, installation):
                    continue
            except Exception:
                logger.warning("Watch Mark writer pass deferred")
                await asyncio.sleep(RECOVERY_SECONDS)
                continue
            try:
                await asyncio.wait_for(wake.wait(), RECOVERY_SECONDS)
            except TimeoutError:
                pass
