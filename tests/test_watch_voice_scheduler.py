"""Owner, target and durable scheduler fence without live requests."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.models.watch_voice import WatchScheduleControl
from app.worker.watch_voice_scheduler import dispatch_once
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.parametrize(
    "case", ["accepted", "wrong_owner", "public_repo", "wrong_workflow", "timeout", "cancelled"]
)
def test_owner_preflight_and_uncertain_dispatch_fails_closed(case):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(WatchScheduleControl.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        posts = []

        async def handler(request):
            if request.method == "POST":
                posts.append(request)
                if case == "timeout":
                    raise httpx.ReadTimeout("synthetic", request=request)
                if case == "cancelled":
                    raise asyncio.CancelledError()
                return httpx.Response(204)
            if request.url.path == "/user":
                return httpx.Response(200, json={"id": 1 if case == "wrong_owner" else 23397294})
            if request.url.path == "/repos/markmalina/mark":
                return httpx.Response(
                    200,
                    json={
                        "id": 1336157811,
                        "private": case != "public_repo",
                        "archived": False,
                        "default_branch": "main",
                        "owner": {"id": 23397294},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "path": ".github/workflows/gate-watch-voice-drain.yml",
                    "state": "disabled" if case == "wrong_workflow" else "active",
                },
            )

        now = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if case == "cancelled":
                with pytest.raises(asyncio.CancelledError):
                    await dispatch_once(factory, client, "synthetic-token", now)
            else:
                outcome = await dispatch_once(factory, client, "synthetic-token", now)
                assert (
                    outcome
                    == {
                        "accepted": "accepted",
                        "wrong_owner": "preflight_rejected",
                        "public_repo": "preflight_rejected",
                        "wrong_workflow": "preflight_rejected",
                        "timeout": "unknown",
                    }[case]
                )
            later = await dispatch_once(
                factory, client, "synthetic-token", now + timedelta(minutes=15)
            )
        async with factory() as session:
            state = await session.scalar(select(WatchScheduleControl))
            assert state is not None
            if case in ("timeout", "cancelled"):
                assert later == "blocked_or_duplicate"
                assert state.state == ("unknown" if case == "timeout" else "inflight")
                assert len(posts) == 1
            elif case in ("wrong_owner", "public_repo", "wrong_workflow"):
                assert later == "preflight_rejected" and len(posts) == 0
            else:
                assert later == "accepted" and len(posts) == 2
        await engine.dispose()

    asyncio.run(scenario())


def test_same_slot_never_dispatches_twice():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(WatchScheduleControl.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(1)
                return httpx.Response(204)
            if request.url.path == "/user":
                return httpx.Response(200, json={"id": 23397294})
            if request.url.path == "/repos/markmalina/mark":
                return httpx.Response(
                    200,
                    json={
                        "id": 1336157811,
                        "private": True,
                        "archived": False,
                        "default_branch": "main",
                        "owner": {"id": 23397294},
                    },
                )
            return httpx.Response(
                200,
                json={"path": ".github/workflows/gate-watch-voice-drain.yml", "state": "active"},
            )

        now = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await dispatch_once(factory, client, "synthetic-token", now) == "accepted"
            assert (
                await dispatch_once(factory, client, "synthetic-token", now + timedelta(minutes=1))
                == "blocked_or_duplicate"
            )
        assert len(posts) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_postgres_prevents_overlapping_slots(tmp_path):
    import os

    from test_watch_voice import database

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("requires PostgreSQL concurrent transactions")

    async def scenario():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchScheduleControl.__table__.create(s.connection())
                )
                await session.commit()
            entered, release = asyncio.Event(), asyncio.Event()
            posts = []

            async def handler(request):
                if request.method == "POST":
                    posts.append(1)
                    entered.set()
                    await release.wait()
                    return httpx.Response(204)
                if request.url.path == "/user":
                    return httpx.Response(200, json={"id": 23397294})
                if request.url.path == "/repos/markmalina/mark":
                    return httpx.Response(
                        200,
                        json={
                            "id": 1336157811,
                            "private": True,
                            "archived": False,
                            "default_branch": "main",
                            "owner": {"id": 23397294},
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "path": ".github/workflows/gate-watch-voice-drain.yml",
                        "state": "active",
                    },
                )

            now = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                first = asyncio.create_task(dispatch_once(factory, client, "synthetic-token", now))
                await asyncio.wait_for(entered.wait(), 5)
                second = await asyncio.wait_for(
                    dispatch_once(factory, client, "synthetic-token", now + timedelta(minutes=15)),
                    5,
                )
                assert second == "blocked_or_duplicate"
                release.set()
                assert await asyncio.wait_for(first, 5) == "accepted"
            assert len(posts) == 1

    asyncio.run(scenario())


def test_status_is_redacted_and_default_is_uninitialized():
    from app.worker.watch_voice_scheduler import read_status

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(WatchScheduleControl.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        assert await read_status(factory) == {
            "state": "uninitialized",
            "last_slot": None,
            "last_outcome": None,
        }
        async with factory() as session:
            session.add(
                WatchScheduleControl(
                    name="private-mark-watch",
                    state="unknown",
                    last_slot=1,
                    last_outcome="unknown",
                    changed_at=datetime.now(UTC),
                )
            )
            await session.commit()
        assert await read_status(factory) == {
            "state": "unknown",
            "last_slot": 1,
            "last_outcome": "unknown",
        }
        await engine.dispose()

    asyncio.run(scenario())


def test_scheduler_is_default_off_without_exposing_token():
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("GATE_WATCH_SCHEDULER_ENABLED", None)
    env["GATE_WATCH_DISPATCH_TOKEN"] = "synthetic-private-token"
    completed = subprocess.run(
        [sys.executable, "-m", "app.worker.watch_voice_scheduler"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.stdout.strip() == "watch_scheduler_disabled"
    assert "synthetic-private-token" not in completed.stdout + completed.stderr
