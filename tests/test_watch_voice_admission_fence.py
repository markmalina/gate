"""Actual admission/claim/no-retry paths; all remote transports are synthetic."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.infrastructure.watch_voice_actions import drain
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.watch_voice import WatchVoiceCursor, WatchVoiceDrainRun, WatchVoiceNote
from app.worker import watch_voice as worker
from sqlalchemy import func, select, text

from test_watch_voice import database, notes
from test_watch_voice_actions import ActionsWorld, action_settings, claims

WORKFLOW = "/repos/markmalina/mark/actions/workflows/gate-watch-voice-drain.yml"


def guarded_transport(world, *, state="active", invalid=None):
    """Also supports the unchanged old fallback test world's HTTP fixture."""

    def handle(request):
        if request.url.path == WORKFLOW:
            if invalid == "timeout":
                raise httpx.ReadTimeout("synthetic", request=request)
            value = {
                "id": 123,
                "path": ".github/workflows/gate-watch-voice-drain.yml",
                "state": state,
            }
            if invalid == "identity":
                value["id"] = 124
            if invalid == "shape":
                value = None
            return httpx.Response(200, json=value)
        response = world.handle(request)
        if request.url.path.startswith("/repos/markmalina/mark/actions/runs/"):
            return httpx.Response(
                response.status_code, json={**response.json(), "workflow_id": 123}
            )
        return response

    return handle


@pytest.mark.parametrize(
    "state,invalid",
    [
        ("disabled_manually", None),
        ("disabled_inactivity", None),
        (None, None),
        ("active", "timeout"),
        ("active", "identity"),
        ("active", "shape"),
    ],
)
def test_disabled_or_unknown_workflow_rejects_before_claim_or_effect(state, invalid):
    world, factory = ActionsWorld(), AsyncMock()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(guarded_transport(world, state=state, invalid=invalid))
        ) as http:
            with pytest.raises(WatchVoiceError):
                await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claims(),
                    job_token="synthetic-job-token",
                )
        factory.assert_not_called()
        assert world.posts == world.provider_calls == 0

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["transcription", "publication"])
def test_interrupted_effect_preserves_claim_and_does_not_repeat_after_restart(
    tmp_path, monkeypatch, phase
):
    world = ActionsWorld()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            started = asyncio.Event()
            never = asyncio.Event()
            transport = guarded_transport(world)

            async def handle(request):
                response = transport(request)
                if (
                    phase == "publication"
                    and request.method == "POST"
                    and "/issues" in request.url.path
                ):
                    # Remote accepted the POST. The local acknowledgement is lost.
                    assert world.posts == 1
                    async with factory() as session:
                        note = await session.scalar(select(WatchVoiceNote))
                        assert note.delivery_state == "sending"
                    started.set()
                    await never.wait()
                return response

            if phase == "transcription":

                class InterruptedTranscriber:
                    def __init__(self, **kwargs):
                        pass

                    async def transcribe(self, command):
                        async with factory() as session:
                            note = await session.scalar(select(WatchVoiceNote))
                            assert note.transcription_state == "processing"
                            assert (
                                await session.scalar(
                                    select(func.count()).select_from(WatchVoiceDrainRun)
                                )
                                == 1
                            )
                        world.provider_calls += 1  # remote accepted, no local result
                        started.set()
                        await never.wait()

                monkeypatch.setattr(
                    worker, "TelegramVoiceTranscriptionService", InterruptedTranscriber
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                task = asyncio.create_task(
                    drain(
                        settings=action_settings(),
                        factory=factory,
                        http=http,
                        claims=claims(),
                        job_token="synthetic-job-token",
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            before = (await notes(factory))[0]
            assert (
                before.transcription_state if phase == "transcription" else before.delivery_state
            ) == ("processing" if phase == "transcription" else "sending")
            preserved = (before.id, before.data, before.sha256, before.file_id, before.revision)
            async with factory() as session:
                cursor = await session.scalar(select(WatchVoiceCursor))
                assert cursor.cursor is None  # in-flight coordinator transaction rolled back
                count = await session.scalar(select(func.count()).select_from(WatchVoiceDrainRun))
                assert count == 1
            world.current_run = "322"
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(guarded_transport(world, state="disabled_manually"))
            ) as http:
                with pytest.raises(WatchVoiceError, match="driver_fenced"):
                    await drain(
                        settings=action_settings(),
                        factory=factory,
                        http=http,
                        claims=claims(run_id="322", jti="late-job"),
                        job_token="synthetic-job-token",
                    )
            async with factory() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(WatchVoiceDrainRun))
                    == count
                )
                note = await session.get(WatchVoiceNote, before.id)
                note.attempted_at = datetime.now(UTC) - timedelta(hours=2)
                await session.commit()
            # Explicit later restore: old uncertain claims quarantine or reconcile;
            # late remote success must never cause a second POST/transcription.
            for index in range(2):
                world.current_run = str(323 + index)
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(guarded_transport(world))
                ) as http:
                    await drain(
                        settings=action_settings(),
                        factory=factory,
                        http=http,
                        claims=claims(run_id=world.current_run, jti=f"restored-{index}"),
                        job_token="synthetic-job-token",
                    )
            after = (await notes(factory))[0]
            assert (after.id, after.data, after.sha256, after.file_id, after.revision) == preserved
            assert world.provider_calls == 1
            assert world.posts == (1 if phase == "publication" else 0)
            assert (
                after.transcription_state if phase == "transcription" else after.delivery_state
            ) == ("unknown" if phase == "transcription" else "delivered")

    asyncio.run(run())


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="actual crash requires disposable PostgreSQL"
)
@pytest.mark.parametrize("phase", ["transcription", "publication"])
def test_process_death_with_active_transaction_preserves_pre_effect_claim(
    tmp_path, monkeypatch, phase
):
    world = ActionsWorld()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                schema = await session.scalar(text("SELECT current_schema()"))
                await session.commit()
            marker = tmp_path / "remote-effect.txt"
            child = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    str(Path(__file__).with_name("watch_fence_crash_child.py")),
                    phase,
                    schema,
                    str(marker),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert child.returncode == 73, child.stderr[-3000:]
            assert marker.read_text() == phase
            async with factory() as session:
                note = await session.scalar(select(WatchVoiceNote))
                assert note.data == world.audio
                assert (
                    note.transcription_state if phase == "transcription" else note.delivery_state
                ) == ("processing" if phase == "transcription" else "sending")
                assert (
                    await session.scalar(select(func.count()).select_from(WatchVoiceDrainRun)) == 1
                )
                cursor = await session.scalar(select(WatchVoiceCursor))
                assert cursor.cursor is None
                note.attempted_at = datetime.now(UTC) - timedelta(hours=2)
                await session.commit()
            world.current_run = "400"
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(guarded_transport(world, state="disabled_manually"))
            ) as http:
                with pytest.raises(WatchVoiceError, match="driver_fenced"):
                    await drain(
                        settings=action_settings(),
                        factory=factory,
                        http=http,
                        claims=claims(run_id="400", jti="post-crash-disabled"),
                        job_token="synthetic-job-token",
                    )
            world.current_run = "401"
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(guarded_transport(world))
            ) as http:
                await drain(
                    settings=action_settings(),
                    factory=factory,
                    http=http,
                    claims=claims(run_id="401", jti="post-crash-restored"),
                    job_token="synthetic-job-token",
                )
            assert world.posts == world.provider_calls == 0
            assert marker.read_text() == phase  # old remote may still finish; never repeat
            after = (await notes(factory))[0]
            assert (
                after.transcription_state if phase == "transcription" else after.delivery_state
            ) == "unknown"

    asyncio.run(run())
