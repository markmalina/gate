"""OpenAI transcription HTTP adapter remains explicit and dormant by default."""

import logging

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



@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "This model's maximum context length was exceeded.",
            "openai_transcription_rejected_context_limit",
        ),
        (
            "Audio duration is longer than the maximum limit.",
            "openai_transcription_rejected_duration_limit",
        ),
        (
            "Maximum file size is 25 MB.",
            "openai_transcription_rejected_file_size_limit",
        ),
        (
            "Invalid audio format.",
            "openai_transcription_rejected_unsupported_format",
        ),
        (
            "The audio file failed to decode.",
            "openai_transcription_rejected_invalid_audio",
        ),
    ],
)
async def test_provider_rejection_is_safely_classified(
    message: str,
    expected: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": message, "private": "must-not-leak"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with pytest.raises(TranscriptionProviderError) as caught:
            await client.transcribe(
                NormalizedVoice(b"wav", "voice.wav", "audio/wav"),
                language="ru",
            )

    assert str(caught.value) == expected
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_structured_provider_rejection_is_safely_classified() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Request rejected.",
                    "type": "invalid_request_error",
                    "code": "audio_decode_error",
                    "param": "file",
                    "private": "must-not-leak",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with pytest.raises(TranscriptionProviderError) as caught:
            await client.transcribe(
                NormalizedVoice(b"mp3", "voice.mp3", "audio/mpeg"),
                language="ru",
            )

    assert str(caught.value) == "openai_transcription_rejected_invalid_audio"
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_provider_rejection_logs_only_allowlisted_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "private transcript fragment must not leak",
                    "type": "invalid_request_error",
                    "code": "invalid_audio",
                    "param": "file",
                    "private": "must-not-leak",
                }
            },
            headers={"x-request-id": "req_voice_diagnostic_400"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with caplog.at_level(logging.WARNING), pytest.raises(TranscriptionProviderError):
            await client.transcribe(
                NormalizedVoice(b"mp3", "voice.mp3", "audio/mpeg"),
                language="ru",
            )

    assert "status=400" in caplog.text
    assert "model=gpt-4o-mini-transcribe" in caplog.text
    assert "request_id=req_voice_diagnostic_400" in caplog.text
    assert "type=invalid_request_error" in caplog.text
    assert "code=invalid_audio" in caplog.text
    assert "param=file" in caplog.text
    assert "private transcript fragment" not in caplog.text
    assert "must-not-leak" not in caplog.text


@pytest.mark.anyio
async def test_adapter_transcribes_ordered_parts_with_previous_context() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        index = len(requests)
        return httpx.Response(
            200,
            json={
                "text": f"часть {index}",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                },
            },
            headers={"x-request-id": f"provider-request-{index}"},
        )

    second = NormalizedVoice(b"mp3-2", "part-002.mp3", "audio/mpeg")
    first = NormalizedVoice(
        b"mp3-1",
        "part-001.mp3",
        "audio/mpeg",
        additional_parts=(second,),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        result = await client.transcribe(first, language="ru")

    assert result.text == "часть 1\nчасть 2"
    assert result.billed_quantity == 220
    assert result.actual_microusd == 350
    assert result.provider_operation_ref == (
        "provider-request-1;provider-request-2"
    )
    assert b'name="prompt"' not in requests[0].content
    assert b'name="prompt"' in requests[1].content
    assert "часть 1".encode() in requests[1].content


@pytest.mark.anyio
async def test_later_part_failure_is_unknown_after_partial_provider_work() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "text": "первая часть",
                    "usage": {
                        "type": "tokens",
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "total_tokens": 110,
                    },
                },
            )
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    voice = NormalizedVoice(
        b"one",
        "one.mp3",
        "audio/mpeg",
        additional_parts=(NormalizedVoice(b"two", "two.mp3", "audio/mpeg"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="secret", enabled=True),
            http=http,
        )
        with pytest.raises(TranscriptionProviderError) as caught:
            await client.transcribe(voice, language="ru")

    assert caught.value.outcome_unknown is True


@pytest.mark.anyio
async def test_combined_output_limit_is_unknown_and_stops_remaining_parts() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "text": "x" * 60_000,
            "usage": {"type": "tokens", "input_tokens": 10,
                      "output_tokens": 10, "total_tokens": 20},
        })

    part = NormalizedVoice(b"audio", "part.mp3", "audio/mpeg")
    source = NormalizedVoice(b"audio", "part.mp3", "audio/mpeg", (part, part))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(api_key="synthetic-key", enabled=True), http=http,
        )
        with pytest.raises(TranscriptionProviderError) as caught:
            await client.transcribe(source, language="ru")
    assert caught.value.outcome_unknown is True
    assert calls == 2
