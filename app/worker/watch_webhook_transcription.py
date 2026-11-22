"""Drain the opt-in Watch transcription queue through the existing shared STT service."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.openai_transcription_wif import transcription_workload_identity
from app.infrastructure.watch_app_auth import WatchAppInstallation, issue_mark_token
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_github import REPOSITORY, WatchVoiceError
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor, WatchVoiceNote
from app.worker.watch_app_runner import app_installation
from app.worker.watch_voice import note_scope, publish_note, transcribe_note

logger = logging.getLogger(__name__)
TRANSCRIPTION_LOCK = 0x57415445
RECOVERY_SECONDS = 60
BATCH_SIZE = 5


def watch_transcription_admission(settings: Settings) -> tuple[str, str] | None:
    value = settings.gate_watch_webhook_transcription_admission.get_secret_value()
    mode, _, event = value.partition(":")
    if (
        mode not in {"canary", "future"}
        or not settings.gate_watch_webhook_enabled
        or not settings.gate_watch_webhook_archive_enabled
        or settings.gate_watch_voice_enabled
        or not settings.gate_watch_voice_data_gate_approved
        or settings.gate_workspace_id != "mark-personal"
        or not settings.dropbox_call_account_id
        or settings.gate_telegram_voice_transcription_model != "gpt-4o-mini-transcribe"
        or settings.openai_api_base_url.rstrip("/") != "https://api.openai.com/v1"
        or not settings.openai_api_key.get_secret_value()
    ):
        return None
    try:
        if str(UUID(event)) == event:
            return mode, event
    except ValueError:
        pass
    return None


async def drain_watch_transcription(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    key: str,
    installation: WatchAppInstallation,
    admission: tuple[str, str],
) -> None:
    mode, event = admission
    scope = note_scope(settings, webhook_only=True)
    async with factory() as lock:
        if not await lock.scalar(select(func.pg_try_advisory_xact_lock(TRANSCRIPTION_LOCK))):
            return
        owner = (settings.gate_workspace_id, settings.dropbox_call_account_id)
        cursor = await lock.get(WatchEarlyCursor, owner)
        if cursor is None or cursor.writer_mode != "app_webhook":
            return
        selector = WatchVoiceNote.id == event
        if mode == "future":
            watermark = await lock.scalar(
                select(WatchVoiceNote.dropbox_modified_at).where(
                    *scope,
                    WatchVoiceNote.id == event,
                    WatchVoiceNote.transcription_state == "transcribed",
                    WatchVoiceNote.delivery_state == "delivered",
                )
            )
            if watermark is None:
                return
            selector = WatchVoiceNote.dropbox_modified_at > watermark

        stale = datetime.now(UTC) - timedelta(hours=1)
        async with factory.begin() as session:
            await session.execute(
                update(WatchVoiceNote)
                .where(
                    *scope, selector,
                    WatchVoiceNote.transcription_state == "processing",
                    WatchVoiceNote.attempted_at < stale,
                )
                .values(transcription_state="unknown", last_error="watch_voice_stale_transcription")
            )
            await session.execute(
                update(WatchVoiceNote)
                .where(
                    *scope, selector,
                    WatchVoiceNote.delivery_state == "sending",
                    WatchVoiceNote.attempted_at < stale,
                )
                .values(delivery_state="unknown", last_error="watch_voice_stale_publication")
            )

        jobs = (await lock.execute(
            select(WatchVoiceNote.id, WatchEarlyCard)
            .join(WatchEarlyCard, and_(
                WatchEarlyCard.id == WatchVoiceNote.id,
                WatchEarlyCard.workspace_key == WatchVoiceNote.workspace_key,
                WatchEarlyCard.account_id == WatchVoiceNote.account_id,
                WatchEarlyCard.file_id == WatchVoiceNote.file_id,
                WatchEarlyCard.revision == WatchVoiceNote.revision,
            ))
            .where(
                *scope, selector,
                WatchEarlyCard.state == "saved",
                or_(
                    WatchVoiceNote.transcription_state == "received",
                    and_(
                        WatchVoiceNote.transcription_state == "transcribed",
                        WatchVoiceNote.delivery_state.in_(("pending", "unknown")),
                    ),
                ),
            )
            .order_by(
                WatchVoiceNote.delivery_state == "unknown",
                WatchVoiceNote.received_at,
                WatchVoiceNote.id,
            )
            .limit(BATCH_SIZE)
        )).all()
        if not jobs:
            return
        if transcription_workload_identity(settings.openai_api_base_url) is None:
            raise WatchVoiceError("watch_webhook_transcription_identity_required")
        token = await issue_mark_token(http, key, installation)
        client = AppInboxClient(http, token, app_slug=installation.slug)
        await client.preflight()
        for note_id, card in jobs:
            number = card.issue_number
            if (
                number is None or number < 1
                or card.issue_url != f"https://github.com/{REPOSITORY}/issues/{number}"
            ):
                logger.warning("Watch transcript delivery has no verified early issue")
                continue
            try:
                await client.verify_early_issue(number, card.body)
                await transcribe_note(settings, factory, http, note_id, webhook_only=True)
                async with factory() as session:
                    note = await session.get(WatchVoiceNote, note_id)
                if note is not None:
                    await publish_note(factory, client, note, early_issue_number=number)
            except WatchVoiceError:
                logger.warning("Watch transcript delivery deferred")


async def run_watch_transcription_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
) -> None:
    admission = watch_transcription_admission(settings)
    key = os.getenv("GATE_WATCH_APP_PRIVATE_KEY", "")
    installation = app_installation()
    if admission is None or not key or installation is None:
        logger.warning("Watch transcription admission invalid")
        return
    async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False) as http:
        while True:
            wake.clear()
            try:
                await drain_watch_transcription(
                    settings, factory, http, key, installation, admission
                )
            except Exception:
                logger.warning("Watch transcription pass deferred")
                await asyncio.sleep(RECOVERY_SECONDS)
                continue
            try:
                await asyncio.wait_for(wake.wait(), RECOVERY_SECONDS)
            except TimeoutError:
                pass
