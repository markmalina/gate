"""Read-only, aggregate Watch queue snapshot; never emits source content."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.watch_voice import WatchEarlyCard, WatchEarlyRejection, WatchScheduleControl
from app.worker.watch_voice_scheduler import CONTROL


async def observe(factory: async_sessionmaker[AsyncSession], now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("timezone_required")
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    WatchEarlyCard.state, func.count(), func.min(WatchEarlyCard.detected_at)
                ).group_by(WatchEarlyCard.state)
            )
        ).all()
        rejections = await session.scalar(select(func.count()).select_from(WatchEarlyRejection))
        control = await session.get(WatchScheduleControl, CONTROL)
    states = {}
    for state, count, oldest in rows:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        states[state] = {
            "count": count,
            "oldest_detected_seconds": max(0, int((now - oldest).total_seconds())),
        }
    return {
        "states": states,
        "rejections_total": rejections,
        "scheduler_state": control.state if control else "uninitialized",
        "scheduler_last_slot": control.last_slot if control else None,
        "scheduler_last_outcome": control.last_outcome if control else None,
    }


async def _main() -> None:
    url = os.environ.get("GATE_WATCH_MONITOR_DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+asyncpg://"):
        print("watch_observe_configuration_missing")
        raise SystemExit(1)
    engine = None
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        print(json.dumps(await observe(async_sessionmaker(engine), datetime.now(UTC))))
    except Exception:
        print("watch_observe_failed")
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(_main())
