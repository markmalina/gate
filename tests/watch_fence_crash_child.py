"""Disposable PostgreSQL child: abruptly exits after a synthetic remote effect."""

import asyncio
import os
import re
import sys
from pathlib import Path

import httpx
import pytest
from app.infrastructure.watch_voice_actions import drain
from app.models.watch_voice import WatchVoiceNote
from app.worker import watch_voice as worker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test_watch_voice_actions import ActionsWorld, action_settings, claims
from test_watch_voice_admission_fence import guarded_transport


async def main():
    phase, schema, marker = sys.argv[1:]
    assert phase in {"transcription", "publication"}
    assert re.fullmatch(r"watch_test_[a-f0-9]{32}", schema)
    url = os.environ["DATABASE_URL"]
    assert url.startswith("postgresql+asyncpg://postgres:postgres@localhost:5432/")
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = ActionsWorld()
    world.patches(pytest.MonkeyPatch())

    async def crash():
        async with factory() as session:
            row = await session.scalar(select(WatchVoiceNote))
            assert (
                row.transcription_state if phase == "transcription" else row.delivery_state
            ) == ("processing" if phase == "transcription" else "sending")
        Path(marker).write_text(phase)
        os._exit(73)  # no finally, rollback, HTTP response or Python cleanup

    class RemoteWithoutReply:
        def __init__(self, **kwargs):
            pass

        async def transcribe(self, command):
            await crash()

    if phase == "transcription":
        worker.TelegramVoiceTranscriptionService = RemoteWithoutReply
    transport = guarded_transport(world)

    async def handle(request):
        response = transport(request)
        if phase == "publication" and request.method == "POST" and "/issues" in request.url.path:
            assert world.posts == 1
            await crash()
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await drain(
            settings=action_settings(),
            factory=factory,
            http=http,
            claims=claims(),
            job_token="synthetic-job-token",
        )
    raise AssertionError("effect boundary not reached")


if __name__ == "__main__":
    asyncio.run(main())
