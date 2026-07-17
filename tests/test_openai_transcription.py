"""OpenAI transcription HTTP adapter remains explicit and dormant by default."""

import httpx
import pytest
from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    TranscriptionProviderError,
)
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)


@pytest.mark.anyio
async def test_disabled_adapter_never_calls_openai() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": "unexpected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="", enabled=False),
            http=http,
        )
        with pytest.raises(
            TranscriptionProviderError,
            match="openai_transcription_disabled",
        ):
            await client.transcribe(
                NormalizedVoice(b"wav", "voice.wav", "audio/wav"),
                language="ru",
            )
    assert calls == 0


@pytest.mark.anyio
async def test_adapter_posts_multipart_audio_and_returns_normalized_text() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "text": "  распознанный текст  ",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 800,
                    "output_tokens": 20,
                    "total_tokens": 820,
                },
            },
            headers={"x-request-id": "provider-request-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        result = await client.transcribe(
            NormalizedVoice(b"wav", "voice.wav", "audio/wav"),
            language="ru",
        )

    assert result.text == "распознанный текст"
    assert result.provider_operation_ref == "provider-request-1"
    assert result.billed_quantity == 820
    assert result.actual_microusd == 1_100
    assert requests[0].url.path == "/v1/audio/transcriptions"
    body = requests[0].content
    assert b'gpt-4o-mini-transcribe' in body
    assert b'name="language"' in body
    assert b"voice.wav" in body
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_transport_failure_is_unknown_and_sanitized() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("contains-sensitive-url")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with pytest.raises(TranscriptionProviderError) as caught:
            await client.transcribe(
                NormalizedVoice(b"wav", "voice.wav", "audio/wav"),
                language=None,
            )

    assert caught.value.outcome_unknown is True
    assert str(caught.value) == "openai_transcription_transport_unknown"


@pytest.mark.anyio
async def test_success_without_billable_usage_fails_closed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "текст без usage"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with pytest.raises(
            TranscriptionProviderError,
            match="openai_transcription_usage_unknown",
        ) as caught:
            await client.transcribe(
                NormalizedVoice(b"wav", "voice.wav", "audio/wav"),
                language="ru",
            )

    assert caught.value.outcome_unknown is True


def test_diarization_model_requires_a_separate_response_contract() -> None:
    with pytest.raises(
        ValueError,
        match="openai_transcription_model_unsupported",
    ):
        OpenAITranscriptionConfig(
            api_key="secret",
            enabled=True,
            model="gpt-4o-transcribe-diarize",
        )

