"""Default-off bounded owner-authenticated wake of the private Mark workflow.

A persistent single-slot fence blocks retries after uncertain or interrupted
POST. This process handles metadata only; the GitHub job provides its own OIDC
and short-lived issue token. No credential or response body is logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.watch_voice import WatchScheduleControl

OWNER_ID = 23397294
REPOSITORY_ID = 1336157811
API = "https://api.github.com"
REPO = "/repos/markmalina/mark"
WORKFLOW = ".github/workflows/gate-watch-voice-drain.yml"
WORKFLOW_ID = "gate-watch-voice-drain.yml"
CONTROL = "private-mark-watch"


class SchedulerError(RuntimeError):
    pass


async def _get_json(
    client: httpx.AsyncClient, path: str, headers: dict[str, str]
) -> dict[str, Any]:
    response = await client.get(API + path, headers=headers, follow_redirects=False, timeout=15.0)
    if response.status_code != 200 or len(response.content) > 262144:
        raise SchedulerError("read_rejected")
    value = response.json()
    if not isinstance(value, dict):
        raise SchedulerError("read_invalid")
    return value


async def dispatch_once(
    factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, token: str, now: datetime
) -> str:
    """One attempt per 15-minute slot; interrupted/uncertain POST fails closed."""
    if now.tzinfo is None or not token or any(c in token for c in "\r\n"):
        raise SchedulerError("invalid_input")
    slot = int(now.timestamp()) // 900
    async with factory() as session:
        await session.execute(
            insert(WatchScheduleControl)
            .values(name=CONTROL, state="idle", last_slot=-1, changed_at=now)
            .on_conflict_do_nothing()
        )
        claimed = await session.execute(
            update(WatchScheduleControl)
            .where(
                WatchScheduleControl.name == CONTROL,
                WatchScheduleControl.state == "idle",
                WatchScheduleControl.last_slot < slot,
            )
            .values(state="inflight", last_slot=slot, changed_at=now, last_outcome=None)
        )
        await session.commit()
        if not claimed.rowcount:  # type: ignore[attr-defined]
            return "blocked_or_duplicate"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    post_attempted = False
    outcome = "preflight_rejected"
    try:
        user = await _get_json(client, "/user", headers)
        if user.get("id") != OWNER_ID:
            raise SchedulerError("preflight_rejected")
        repo = await _get_json(client, REPO, headers)
        if (
            repo.get("id") != REPOSITORY_ID
            or repo.get("private") is not True
            or repo.get("archived") is not False
            or repo.get("default_branch") != "main"
            or repo.get("owner", {}).get("id") != OWNER_ID
        ):
            raise SchedulerError("preflight_rejected")
        workflow = await _get_json(client, f"{REPO}/actions/workflows/{WORKFLOW_ID}", headers)
        if workflow.get("path") != WORKFLOW or workflow.get("state") != "active":
            raise SchedulerError("preflight_rejected")
        post_attempted = True
        response = await client.post(
            f"{API}{REPO}/actions/workflows/{WORKFLOW_ID}/dispatches",
            headers=headers,
            json={"ref": "main"},
            follow_redirects=False,
            timeout=15.0,
        )
        if response.status_code == 204:
            outcome = "accepted"
        elif response.status_code in (400, 401, 403, 404, 422):
            outcome = "rejected"
        else:
            outcome = "unknown"
    except (httpx.HTTPError, ValueError, SchedulerError, TypeError, AttributeError):
        outcome = "unknown" if post_attempted else "preflight_rejected"
    async with factory() as session:
        await session.execute(
            update(WatchScheduleControl)
            .where(
                WatchScheduleControl.name == CONTROL,
                WatchScheduleControl.state == "inflight",
                WatchScheduleControl.last_slot == slot,
            )
            .values(
                state="unknown" if outcome == "unknown" else "idle",
                last_outcome=outcome,
                changed_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return outcome


async def read_status(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Expose only the fixed control state, never credentials or response bodies."""
    async with factory() as session:
        row = await session.get(WatchScheduleControl, CONTROL)
        if row is None:
            return {"state": "uninitialized", "last_slot": None, "last_outcome": None}
        return {"state": row.state, "last_slot": row.last_slot, "last_outcome": row.last_outcome}


async def _main() -> None:
    status_only = sys.argv[1:] == ["--status"]
    if sys.argv[1:] and not status_only:
        print("watch_scheduler_arguments_invalid")
        raise SystemExit(1)
    if (
        not status_only
        and os.environ.get("GATE_WATCH_SCHEDULER_ENABLED", "false").lower() != "true"
    ):
        print("watch_scheduler_disabled")
        return
    token = os.environ.get("GATE_WATCH_DISPATCH_TOKEN", "")
    url = os.environ.get("DATABASE_URL", "")
    if not url or (not status_only and not token):
        print("watch_scheduler_configuration_missing")
        raise SystemExit(1)
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+asyncpg://"):
        print("watch_scheduler_configuration_invalid")
        raise SystemExit(1)
    engine = None
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        if status_only:
            print(json.dumps(await read_status(factory), separators=(",", ":")))
            return
        async with httpx.AsyncClient() as client:
            result = await dispatch_once(factory, client, token, datetime.now(UTC))
        print("watch_scheduler_" + result)
        if result not in ("accepted", "blocked_or_duplicate"):
            raise SystemExit(1)
    except Exception:
        # Exceptions can contain database URLs, headers or response bodies.
        print("watch_scheduler_failed")
        raise SystemExit(1) from None
    finally:
        try:
            if engine is not None:
                await engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(_main())
