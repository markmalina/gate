"""Temporary owner-only bridge for bounded chunked OpenAI transcription."""

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

_PATH = "/ops/owner-one-off/transcription/2026-08-03-chunks"
_EXPECTED_CHUNK_SHA256 = {
    0: "daf06eac036519e911f2efa7b122902f9b55141c559abcb34a58317ef80cb6ca",
    1: "969dd82c11595d34bdca7e2957328c676d655ae5024618be2f3ef5e43b2f607a",
    2: "b7439505d11696c553985e65a5c47bd7c48d5d51d895cbb75aafc598b675b0fd",
    3: "da709ba7e876e4d10410a73b59cb7d92be6d57a65429751d6faa44ba5e40f711",
    4: "217d2056532ba3327f81c7741eaaf7f4797bcbf68f00aae1469fee7d9d2d1135",
    5: "a698155516e49cc3922ebb8a3ebb655789f3e3ed5075e41f0a9149f3b8d2be3a",
}
_EXPECTED_REQUEST_TOKEN_SHA256 = (
    "824a159df865e9f5c2649438706118e8fcbc1bd30674cd7207b1832efa19807d"
)
_MAX_BODY_BYTES = 4_000_000
_EXPIRES_AT = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
_attempt_lock = asyncio.Lock()
_provider_attempted_chunks: set[int] = set()


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
async def transcribe_exact_owner_chunk(
    request: Request,
    x_owner_transcription_key: Annotated[
        str | None,
        Header(alias="X-Owner-Transcription-Key"),
    ] = None,
    x_owner_chunk_index: Annotated[
        int | None,
        Header(alias="X-Owner-Chunk-Index"),
    ] = None,
) -> JSONResponse:
    """Transcribe each of six exact owner-supplied chunks at most once."""

    _require_request_token(x_owner_transcription_key)
    if _now() >= _EXPIRES_AT:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="One-off transcription endpoint expired",
        )
    if x_owner_chunk_index not in _EXPECTED_CHUNK_SHA256:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Chunk index is not authorized",
        )
    chunk_index = x_owner_chunk_index

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
    actual_sha256 = hashlib.sha256(body).hexdigest()
    expected_sha256 = _EXPECTED_CHUNK_SHA256[chunk_index]
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio digest does not match the authorized chunk",
        )

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI transcription credential is not configured",
        )

    async with _attempt_lock:
        if chunk_index in _provider_attempted_chunks:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider attempt for this chunk is already consumed",
            )
        _provider_attempted_chunks.add(chunk_index)
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
                    NormalizedVoice(
                        body,
                        f"authorized-source-part-{chunk_index}.m4a",
                        "audio/mp4",
                    ),
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
            "chunk_index": chunk_index,
            "text": result.text,
            "model": "gpt-4o-mini-transcribe",
            "language": "ru",
            "billed_quantity": result.billed_quantity,
            "actual_microusd": result.actual_microusd,
            "attempts": 1,
        },
        headers={"Cache-Control": "no-store"},
    )
