"""Bounded tests for the temporary exact-owner transcription bridge."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from app.api import owner_one_off_transcription as target
from app.application.telegram_voice_transcription import ProviderTranscript
from fastapi import HTTPException
from starlette.requests import Request


def _request(body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": target._PATH,
            "raw_path": target._PATH.encode(),
            "query_string": b"",
            "headers": [(b"content-length", str(len(body)).encode())],
            "client": ("test", 1),
            "server": ("gate", 443),
        },
        receive,
    )


@pytest.mark.anyio
async def test_chunk_bridge_consumes_each_exact_chunk_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = {0: b"exact-owner-audio-0", 1: b"exact-owner-audio-1"}
    token = "test-one-off-token"
    calls = 0

    async def transcribe(*_args: object, **_kwargs: object) -> ProviderTranscript:
        nonlocal calls
        calls += 1
        return ProviderTranscript(
            text=f"распознанный текст {calls}",
            billed_quantity=123,
            actual_microusd=456,
        )

    monkeypatch.setattr(target, "_provider_attempted_chunks", set())
    monkeypatch.setattr(
        target,
        "_EXPECTED_CHUNK_SHA256",
        {index: hashlib.sha256(body).hexdigest() for index, body in chunks.items()},
    )
    monkeypatch.setattr(
        target,
        "_EXPECTED_REQUEST_TOKEN_SHA256",
        hashlib.sha256(token.encode()).hexdigest(),
    )
    monkeypatch.setattr(
        target,
        "_now",
        lambda: datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-secret")
    monkeypatch.setattr(target.OpenAITranscriptionClient, "transcribe", transcribe)

    response = await target.transcribe_exact_owner_chunk(
        _request(chunks[0]),
        x_owner_transcription_key=token,
        x_owner_chunk_index=0,
    )
    payload = json.loads(response.body)

    assert payload["text"] == "распознанный текст 1"
    assert payload["chunk_index"] == 0
    assert payload["attempts"] == 1
    assert response.headers["cache-control"] == "no-store"

    with pytest.raises(HTTPException) as replay:
        await target.transcribe_exact_owner_chunk(
            _request(chunks[0]),
            x_owner_transcription_key=token,
            x_owner_chunk_index=0,
        )
    assert replay.value.status_code == 409

    second = await target.transcribe_exact_owner_chunk(
        _request(chunks[1]),
        x_owner_transcription_key=token,
        x_owner_chunk_index=1,
    )
    assert json.loads(second.body)["chunk_index"] == 1
    assert calls == 2


@pytest.mark.anyio
async def test_chunk_bridge_rejects_wrong_token_index_and_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-one-off-token"
    exact = b"exact-owner-audio"
    monkeypatch.setattr(target, "_provider_attempted_chunks", set())
    monkeypatch.setattr(
        target,
        "_EXPECTED_CHUNK_SHA256",
        {0: hashlib.sha256(exact).hexdigest()},
    )
    monkeypatch.setattr(
        target,
        "_EXPECTED_REQUEST_TOKEN_SHA256",
        hashlib.sha256(token.encode()).hexdigest(),
    )
    monkeypatch.setattr(
        target,
        "_now",
        lambda: datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-secret")

    with pytest.raises(HTTPException) as wrong_token:
        await target.transcribe_exact_owner_chunk(
            _request(exact),
            x_owner_transcription_key="wrong",
            x_owner_chunk_index=0,
        )
    assert wrong_token.value.status_code == 403

    with pytest.raises(HTTPException) as wrong_index:
        await target.transcribe_exact_owner_chunk(
            _request(exact),
            x_owner_transcription_key=token,
            x_owner_chunk_index=6,
        )
    assert wrong_index.value.status_code == 422

    with pytest.raises(HTTPException) as wrong_audio:
        await target.transcribe_exact_owner_chunk(
            _request(b"different"),
            x_owner_transcription_key=token,
            x_owner_chunk_index=0,
        )
    assert wrong_audio.value.status_code == 422
    assert target._provider_attempted_chunks == set()
