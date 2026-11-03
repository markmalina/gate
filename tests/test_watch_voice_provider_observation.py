"""Metadata callbacks preserve the pinned provider request and never retry it."""

import asyncio

import httpx
import pytest
from app.application.telegram_voice_transcription import NormalizedVoice
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.watch_voice_journal import provider_observation
from app.models.watch_voice import WatchVoiceNote
from sqlalchemy import select
from tests.test_watch_voice import database
from tests.test_watch_voice_inventory import note


def test_each_part_is_observed_and_counters_survive_new_session(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.openai.com/v1/audio/transcriptions"
        assert b"gpt-4o-mini-transcribe" in request.content
        return httpx.Response(
            200,
            json={
                "text": "synthetic",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    async def scenario():
        async with database(tmp_path) as factory:
            async with factory() as session:
                row = WatchVoiceNote(
                    **note(
                        1,
                        provider_request_count=0,
                        provider_elapsed_ms=0,
                        provider_finished_count=0,
                    )
                )
                session.add(row)
                await session.commit()

            async def observe(phase, elapsed_ms):
                await provider_observation(factory, row.id, phase, elapsed_ms)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                client = OpenAITranscriptionClient(
                    config=OpenAITranscriptionConfig(enabled=True, api_key="synthetic"),
                    http=http,
                    part_observer=observe,
                )
                result = await client.transcribe(
                    NormalizedVoice(
                        b"synthetic",
                        "voice.mp3",
                        "audio/mpeg",
                        additional_parts=(NormalizedVoice(b"second", "voice2.mp3", "audio/mpeg"),),
                    ),
                    language="ru",
                )
                assert result.text == "synthetic\nsynthetic"
            async with factory() as reader:
                saved = await reader.scalar(select(WatchVoiceNote))
                assert saved.provider_request_count == 2
                assert saved.provider_finished_count == 2
                assert (
                    saved.provider_started_at is not None and saved.provider_finished_at is not None
                )
                assert saved.provider_elapsed_ms >= 0
        assert len(requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("failing_phase", ["started", "finished"])
def test_start_failure_blocks_io_finish_failure_preserves_text(failing_phase):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "text": "complete",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    async def observe(phase, elapsed_ms):
        if phase == failing_phase:
            raise RuntimeError("synthetic observation failure")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = OpenAITranscriptionClient(
                config=OpenAITranscriptionConfig(enabled=True, api_key="synthetic"),
                http=http,
                part_observer=observe,
            )
            if failing_phase == "started":
                with pytest.raises(RuntimeError):
                    await client.transcribe(
                        NormalizedVoice(b"audio", "a.mp3", "audio/mpeg"), language="ru"
                    )
                assert calls == []
            else:
                result = await client.transcribe(
                    NormalizedVoice(b"audio", "a.mp3", "audio/mpeg"), language="ru"
                )
                assert result.text == "complete" and len(calls) == 1

    asyncio.run(scenario())
