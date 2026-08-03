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
async def test_one_off_bridge_accepts_only_exact_input_and_consumes_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"exact-owner-audio"
    token = "test-one-off-token"
    calls = 0

    async def transcribe(*_args: object, **_kwargs: object) -> ProviderTranscript:
        nonlocal calls
        calls += 1
        return ProviderTranscript(
            text="распознанный текст",
            billed_quantity=123,
            actual_microusd=456,
        )

    monkeypatch.setattr(target, "_provider_attempted", False)
    monkeypatch.setattr(target, "_EXPECTED_AUDIO_SHA256", hashlib.sha256(body).hexdigest())
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

    response = await target.transcribe_exact_owner_audio(
        _request(body),
        x_owner_transcription_key=token,
    )
    payload = json.loads(response.body)

    assert payload["text"] == "распознанный текст"
    assert payload["attempts"] == 1
    assert response.headers["cache-control"] == "no-store"
    assert calls == 1

    with pytest.raises(HTTPException) as replay:
        await target.transcribe_exact_owner_audio(
            _request(body),
            x_owner_transcription_key=token,
        )
    assert replay.value.status_code == 409
    assert calls == 1


@pytest.mark.anyio
async def test_one_off_bridge_rejects_wrong_token_and_wrong_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-one-off-token"
    exact = b"exact-owner-audio"
    monkeypatch.setattr(target, "_provider_attempted", False)
    monkeypatch.setattr(target, "_EXPECTED_AUDIO_SHA256", hashlib.sha256(exact).hexdigest())
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
        await target.transcribe_exact_owner_audio(
            _request(exact),
            x_owner_transcription_key="wrong",
        )
    assert wrong_token.value.status_code == 403

    with pytest.raises(HTTPException) as wrong_audio:
        await target.transcribe_exact_owner_audio(
            _request(b"different"),
            x_owner_transcription_key=token,
        )
    assert wrong_audio.value.status_code == 422
    assert target._provider_attempted is False
