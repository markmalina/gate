"""Run owner Watch speech recognition from the committed Gate archive."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.openai_transcription import (
    WATCH_BENCHMARK_MAX_SECONDS,
    WATCH_BENCHMARK_SHA256,
    WATCH_CANARY_MAX_BYTES,
    WATCH_CANARY_MAX_SECONDS,
)
from app.infrastructure.openai_transcription_wif import transcription_workload_identity
from app.models.watch_voice import WatchVoiceNote, next_mark_publish_version
from app.worker.watch_voice import note_scope, timestamp, transcribe_note

logger = logging.getLogger(__name__)
TRANSCRIPTION_LOCK = 0x57415445
ERROR_RETRY_SECONDS = 60
BATCH_SIZE = 5
CANARY_MAX_DURATION_SECONDS = 30


@dataclass(frozen=True)
class WatchModelCanary:
    after: datetime
    filename: str
    sha256: str


@dataclass(frozen=True)
class TranscriptionAdmission:
    mode: str
    after: datetime
    backlog: tuple[tuple[str, str], ...]
    model_canary: WatchModelCanary | None = None


def watch_transcription_admission(settings: Settings) -> TranscriptionAdmission | None:
    if (
        not settings.gate_watch_webhook_enabled
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
    value = settings.gate_watch_webhook_transcription_admission.get_secret_value()
    try:
        config = json.loads(value)
        if not isinstance(config, dict) or set(config) - {"mode", "after", "backlog"}:
            return None
        mode, after = config.get("mode"), config.get("after")
        backlog = config.get("backlog", [])
        if (
            mode not in ("next", "live")
            or not isinstance(after, str)
            or not isinstance(backlog, list)
            or len(backlog) > 20
            or (mode == "next" and backlog)
            or not all(
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], str)
                and re.fullmatch(r"id:[A-Za-z0-9_-]{1,252}", item[0])
                and re.fullmatch(r"[a-f0-9]{1,255}", item[1])
                for item in backlog
            )
        ):
            return None
        cutoff = timestamp(after)
        candidate = None
        model_gate = settings.gate_watch_gpt_transcribe_canary.get_secret_value()
        if model_gate:
            spec = json.loads(model_gate)
            if mode != "live" or not isinstance(spec, dict) or set(spec) != {
                "after", "filename", "sha256",
            }:
                return None
            test_after, filename, digest = spec["after"], spec["filename"], spec["sha256"]
            if (
                not isinstance(test_after, str)
                or not isinstance(filename, str)
                or not re.fullmatch(r"gate-gpt-transcribe-canary-[a-f0-9]{12}\.m4a", filename)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                return None
            candidate = WatchModelCanary(timestamp(test_after), filename, digest)
            if candidate.after < cutoff:
                return None
        return TranscriptionAdmission(
            mode, cutoff, tuple((item[0], item[1]) for item in backlog), candidate,
        )
    except (ValueError, RuntimeError):
        return None


async def drain_watch_transcription(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    admission: TranscriptionAdmission,
    mark_wake: asyncio.Event | None = None,
) -> bool:
    scope = note_scope(settings, ignore_legacy_selector=True)
    eligible = and_(
        WatchVoiceNote.webhook_archived.is_(True),
        WatchVoiceNote.dropbox_modified_at >= admission.after,
        WatchVoiceNote.received_at >= admission.after,
    )
    ordinary_filename = ~WatchVoiceNote.filename.like("gate-gpt-transcribe-canary-%.m4a")
    async with factory() as lock:
        # Concurrent wakeups wait their turn instead of dropping a committed event.
        await lock.execute(select(func.pg_advisory_xact_lock(TRANSCRIPTION_LOCK)))
        if admission.mode == "next":
            first_id = await lock.scalar(
                select(WatchVoiceNote.id)
                .where(*scope, eligible, ordinary_filename)
                .order_by(WatchVoiceNote.received_at, WatchVoiceNote.id)
                .limit(1)
            )
            if first_id is None:
                return False
            selector = and_(
                WatchVoiceNote.id == first_id,
                WatchVoiceNote.duration_seconds <= CANARY_MAX_DURATION_SECONDS,
            )
        else:
            selector = or_(
                eligible,
                tuple_(WatchVoiceNote.file_id, WatchVoiceNote.revision).in_(admission.backlog),
            )
        selector = and_(selector, ordinary_filename)

        canary_id = None
        canary_max_seconds = WATCH_CANARY_MAX_SECONDS
        if (canary := admission.model_canary) is not None:
            if canary.sha256 == WATCH_BENCHMARK_SHA256:
                canary_max_seconds = WATCH_BENCHMARK_MAX_SECONDS
            canary_filename = WatchVoiceNote.filename == canary.filename
            canary_id = await lock.scalar(
                select(WatchVoiceNote.id)
                .where(
                    *scope, eligible, canary_filename,
                    WatchVoiceNote.sha256 == canary.sha256,
                    WatchVoiceNote.received_at >= canary.after,
                    WatchVoiceNote.dropbox_modified_at >= canary.after,
                )
                .order_by(WatchVoiceNote.received_at, WatchVoiceNote.id)
                .limit(1)
            )
            selector = or_(
                selector,
                and_(
                    WatchVoiceNote.id == canary_id,
                    WatchVoiceNote.duration_seconds <= canary_max_seconds,
                    WatchVoiceNote.size_bytes <= WATCH_CANARY_MAX_BYTES,
                ),
            )

        stale = datetime.now(UTC) - timedelta(hours=1)
        async with factory.begin() as session:
            stale_marked = await session.execute(
                update(WatchVoiceNote)
                .where(
                    *scope, selector,
                    WatchVoiceNote.transcription_state == "processing",
                    WatchVoiceNote.attempted_at < stale,
                )
                .values(
                    transcription_state="unknown",
                    last_error="watch_voice_stale_transcription",
                    transcription_stopped_at=datetime.now(UTC),
                    transcription_stop_reason="watch_voice_stale_transcription",
                    mark_publish_version=next_mark_publish_version(),
                )
            )

        if mark_wake is not None and stale_marked.rowcount:  # type: ignore[attr-defined]
            mark_wake.set()

        jobs = (await lock.scalars(
            select(WatchVoiceNote.id)
            .where(*scope, selector, WatchVoiceNote.transcription_state == "received")
            .order_by(WatchVoiceNote.received_at, WatchVoiceNote.id)
            .limit(BATCH_SIZE)
        )).all()
        if not jobs:
            return False
        if transcription_workload_identity(settings.openai_api_base_url) is None:
            logger.warning("Watch Gate transcription identity unavailable")
            return False
        for note_id in jobs:
            await transcribe_note(
                settings, factory, http, note_id,
                ignore_legacy_selector=True,
                canary_max_seconds=canary_max_seconds if note_id == canary_id else None,
                mark_wake=mark_wake,
            )
        return len(jobs) == BATCH_SIZE


async def run_watch_transcription_loop(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    wake: asyncio.Event,
    mark_wake: asyncio.Event | None = None,
) -> None:
    admission = watch_transcription_admission(settings)
    if admission is None:
        logger.warning("Watch Gate transcription admission invalid")
        return
    async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False) as http:
        while True:
            wake.clear()
            try:
                if await drain_watch_transcription(settings, factory, http, admission, mark_wake):
                    continue
            except Exception:
                logger.warning("Watch Gate transcription pass deferred")
                await asyncio.sleep(ERROR_RETRY_SECONDS)
                continue
            await wake.wait()
