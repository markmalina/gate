"""Durable payload-free lifecycle evidence. This journal grants no external effect."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.watch_voice import WatchVoiceCycle, WatchVoiceNote

CycleBinding = tuple[async_sessionmaker[AsyncSession], str]
CURRENT_CYCLE: ContextVar[CycleBinding | None] = ContextVar("watch_voice_cycle", default=None)


def safe_code(value: object) -> str:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value)
        else "watch_voice_cycle_failed"
    )


async def start_cycle(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    driver: str,
) -> str:
    now = datetime.now(UTC)
    cycle_id = str(uuid.uuid4())
    sha = settings.deployment_sha
    async with factory() as session:
        session.add(
            WatchVoiceCycle(
                id=cycle_id,
                workspace_key=settings.gate_workspace_id,
                account_id=settings.dropbox_call_account_id,
                driver=driver,
                source_sha=sha if re.fullmatch(r"[a-f0-9]{40}", sha) else None,
                owns_cursor=False,
                started_at=now,
                heartbeat_at=now,
                phase="admission",
                deadline_at=now + timedelta(seconds=180),
                outcome="running",
            )
        )
        await session.commit()
    return cycle_id


async def pulse(phase: str, *, seconds: int = 90, **fields: Any) -> None:
    binding = CURRENT_CYCLE.get()
    if binding is None:
        return
    factory, cycle_id = binding
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            update(WatchVoiceCycle)
            .where(
                WatchVoiceCycle.id == cycle_id,
                WatchVoiceCycle.outcome == "running",
            )
            .values(
                phase=phase,
                heartbeat_at=now,
                deadline_at=now + timedelta(seconds=seconds),
                **fields,
            )
        )
        await session.commit()


async def finish_cycle(outcome: str, error: object = None) -> None:
    binding = CURRENT_CYCLE.get()
    if binding is None:
        return
    factory, cycle_id = binding
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            update(WatchVoiceCycle)
            .where(
                WatchVoiceCycle.id == cycle_id,
                WatchVoiceCycle.outcome == "running",
            )
            .values(
                outcome=outcome,
                finished_at=now,
                heartbeat_at=now,
                deadline_at=now + timedelta(seconds=120),
                error_code=safe_code(error) if error is not None else None,
            )
        )
        await session.commit()


async def processing_started(
    factory: async_sessionmaker[AsyncSession],
    note_id: str,
    duration: int,
    *,
    first_normal_claim: bool = False,
) -> None:
    now = datetime.now(UTC)
    # Normalization precedes the first provider attempt. Each provider part then
    # publishes its own bounded deadline. No long audio is declared stuck at120s.
    await pulse("normalizing", seconds=max(180, 60 * ((duration + 179) // 180) + 90))
    async with factory() as session:
        await session.execute(
            update(WatchVoiceNote)
            .where(WatchVoiceNote.id == note_id)
            .values(
                processing_started_at=func.coalesce(WatchVoiceNote.processing_started_at, now),
                processing_attempt_count=(
                    func.coalesce(WatchVoiceNote.processing_attempt_count, 0) + 1
                    if first_normal_claim
                    else WatchVoiceNote.processing_attempt_count + 1
                ),
                **(
                    {
                        "provider_request_count": func.coalesce(
                            WatchVoiceNote.provider_request_count, 0
                        ),
                        "provider_finished_count": func.coalesce(
                            WatchVoiceNote.provider_finished_count, 0
                        ),
                        "provider_elapsed_ms": func.coalesce(WatchVoiceNote.provider_elapsed_ms, 0),
                    }
                    if first_normal_claim
                    else {}
                ),
            )
        )
        await session.commit()


async def provider_observation(
    factory: async_sessionmaker[AsyncSession],
    note_id: str,
    phase: str,
    elapsed_ms: int,
) -> None:
    now = datetime.now(UTC)
    await pulse("provider_request" if phase == "started" else "provider_result", seconds=75)
    values = (
        {
            "provider_started_at": func.coalesce(WatchVoiceNote.provider_started_at, now),
            "provider_request_count": WatchVoiceNote.provider_request_count + 1,
        }
        if phase == "started"
        else {
            "provider_finished_at": now,
            "provider_finished_count": WatchVoiceNote.provider_finished_count + 1,
            "provider_elapsed_ms": WatchVoiceNote.provider_elapsed_ms + elapsed_ms,
        }
    )
    async with factory() as session:
        await session.execute(
            update(WatchVoiceNote).where(WatchVoiceNote.id == note_id).values(**values)
        )
        await session.commit()


async def latest_cycle(
    session: AsyncSession,
    settings: Settings,
) -> WatchVoiceCycle | None:
    return await session.scalar(
        select(WatchVoiceCycle)
        .where(
            WatchVoiceCycle.workspace_key == settings.gate_workspace_id,
            WatchVoiceCycle.account_id == settings.dropbox_call_account_id,
            or_(WatchVoiceCycle.owns_cursor.is_(True), WatchVoiceCycle.outcome == "failure"),
        )
        .order_by(WatchVoiceCycle.started_at.desc(), WatchVoiceCycle.id.desc())
        .limit(1)
    )
