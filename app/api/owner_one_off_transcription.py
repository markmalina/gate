"""Temporary owner-only bridge for one bounded OpenAI transcription."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    TranscriptionProviderError,
)
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)

router = APIRouter()

_PATH = "/ops/owner-one-off/transcription/2026-08-03"
_EXPECTED_AUDIO_SHA256 = "c400f62b24429ae38d755632f81fa945624749b5cfaf8adb2744d431747ca5a9"
_EXPECTED_REQUEST_TOKEN_SHA256 = (
    "0617262b6b4db9d8f5d2b5e09c87bc7ad1b14f980704ed9a9effa158c2daf6ec"
)
_MAX_BODY_BYTES = 25_000_000
_EXPIRES_AT = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
_attempt_lock = asyncio.Lock()
_provider_attempted = False


def _now() -> datetime:
    return datetime.now(UTC)


def _require_request_token(value: str | None) -> None:
    candidate = hashlib.sha256((value or "").encode("utf-8")).hexdigest()
    if not hmac.compare_digest(candidate, _EXPECTED_REQUEST_TOKEN_SHA256):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid one-off transcription credential",
        )


@router.post(_PATH)
async def transcribe_exact_owner_audio(
    request: Request,
    x_owner_transcription_key: Annotated[
        str | None,
        Header(alias="X-Owner-Transcription-Key"),
    ] = None,
) -> JSONResponse:
    """Transcribe one exact owner-supplied file, once, before the hard expiry."""

    global _provider_attempted

    _require_request_token(x_owner_transcription_key)
    if _now() >= _EXPIRES_AT:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="One-off transcription endpoint expired",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length",
            ) from exc
        if declared_size <= 0 or declared_size > _MAX_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio body exceeds the bounded limit",
            )

    body = await request.body()
    if not body or len(body) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio body exceeds the bounded limit",
        )
    if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), _EXPECTED_AUDIO_SHA256):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio digest does not match the authorized input",
        )

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI transcription credential is not configured",
        )

    async with _attempt_lock:
        if _provider_attempted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One-off provider attempt already consumed",
            )
        _provider_attempted = True
        async with httpx.AsyncClient() as http:
            client = OpenAITranscriptionClient(
                config=OpenAITranscriptionConfig(
                    api_key=api_key,
                    enabled=True,
                    model="gpt-4o-mini-transcribe",
                    timeout_seconds=300,
                ),
                http=http,
            )
            try:
                result = await client.transcribe(
                    NormalizedVoice(body, "authorized-source.m4a", "audio/mp4"),
                    language="ru",
                )
            except TranscriptionProviderError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": exc.code,
                        "outcome_unknown": exc.outcome_unknown,
                    },
                ) from None

    return JSONResponse(
        {
            "text": result.text,
            "model": "gpt-4o-mini-transcribe",
            "language": "ru",
            "billed_quantity": result.billed_quantity,
            "actual_microusd": result.actual_microusd,
            "attempts": 1,
        },
        headers={"Cache-Control": "no-store"},
    )
