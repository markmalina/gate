from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.api.mcp_transcription_routes import _tool_definitions
from app.application.alpha_transcription_product import (
    StoredArtifact,
    analyze_transcript,
    build_manifest,
    build_part_windows,
    clean_transcript,
    merge_overlapping_transcripts,
)
from app.application.telegram_voice_transcription import TranscriptionProviderError
from app.infrastructure.openai_transcription_parts import (
    OpenAITranscriptionPartClient,
    OpenAITranscriptionPartConfig,
)


def test_twenty_two_minute_recording_is_six_overlapping_parts() -> None:
    parts = build_part_windows(1_350_635)
    assert len(parts) == 6
    assert parts[0].start_millis == 0
    assert parts[0].end_millis == 230_000
    assert parts[1].start_millis == 225_000
    assert parts[-1].end_millis == 1_350_635
    assert all(
        current.end_millis - following.start_millis == 5_000
        for current, following in zip(parts, parts[1:], strict=False)
    )


def test_overlap_merge_removes_only_confirmed_word_sequence() -> None:
    merged = merge_overlapping_transcripts(
        [
            "Первая мысль. Мы договорились проверить полный результат завтра утром.",
            "мы договорились проверить полный результат завтра утром. "
            "После этого отправим файл.",
        ]
    )
    assert merged.count("договорились проверить") == 1
    assert merged.endswith("После этого отправим файл.")


def test_clean_and_analysis_remain_separate_from_verbatim() -> None:
    verbatim = (
        "Нам нужно нужно нужно проверить запись. "
        "Мы договорились отправить результат."
    )
    clean = clean_transcript(verbatim)
    analysis = analyze_transcript(verbatim)
    assert verbatim != clean
    assert "нужно нужно нужно" not in clean
    assert "## Договорённости" in analysis
    assert "Мы договорились отправить результат." in analysis
    assert "> Анализ извлечён только из текста записи" in analysis


def test_manifest_records_private_artifacts_and_cost() -> None:
    now = datetime(2026, 8, 3, 11, 0, tzinfo=UTC)
    body = build_manifest(
        job_id="11111111-1111-1111-1111-111111111111",
        title="Тест",
        source_filename="test.m4a",
        source_path="/Alpha/Transcriptions/1/source/test.m4a",
        source_sha256="a" * 64,
        source_size_bytes=123,
        duration_millis=1000,
        model="gpt-4o-mini-transcribe",
        language="ru",
        chunk_count=1,
        billed_quantity=10,
        actual_microusd=20,
        artifacts={
            "verbatim.md": StoredArtifact(
                path="/Alpha/Transcriptions/1/verbatim.md",
                sha256="b" * 64,
                size_bytes=42,
            )
        },
        created_at=now,
        completed_at=now,
    )
    text = body.decode("utf-8")
    assert '"schema": "gate.alpha-transcription.manifest@1"' in text
    assert '"actual_microusd": 20' in text
    assert '"private_path": "/Alpha/Transcriptions/1/verbatim.md"' in text


def test_mcp_surface_exposes_complete_owner_workflow() -> None:
    tools = _tool_definitions()
    names = {tool["name"] for tool in tools}
    assert names == {
        "gate_alpha_create_transcription",
        "gate_alpha_get_transcription",
        "gate_alpha_list_transcriptions",
        "gate_alpha_get_transcription_artifact",
        "gate_alpha_retry_transcription",
    }
    assert all(tool["outputSchema"]["type"] == "object" for tool in tools)
    assert all(tool["securitySchemes"] for tool in tools)


@pytest.mark.anyio
async def test_part_client_returns_usage_and_request_ref() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(
            200,
            headers={"x-request-id": "req_test_1"},
            json={
                "text": "полный текст",
                "usage": {
                    "type": "tokens",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionPartClient(
            config=OpenAITranscriptionPartConfig(api_key="secret"),
            http=http,
        )
        result = await client.transcribe_part(
            data=b"audio",
            filename="part.mp3",
            language="ru",
            continuity_prompt="предыдущий контекст",
        )
    assert result.text == "полный текст"
    assert result.provider_operation_ref == "req_test_1"
    assert result.billed_quantity == 15
    assert result.actual_microusd > 0


@pytest.mark.anyio
async def test_transport_outcome_is_unknown_after_exactly_one_call() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITranscriptionPartClient(
            config=OpenAITranscriptionPartConfig(api_key="secret"),
            http=http,
        )
        with pytest.raises(TranscriptionProviderError) as captured:
            await client.transcribe_part(
                data=b"audio",
                filename="part.mp3",
                language="ru",
                continuity_prompt=None,
            )
    assert captured.value.outcome_unknown is True
    assert calls == 1
