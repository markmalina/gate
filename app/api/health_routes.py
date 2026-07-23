"""Process liveness and deployment readiness routes."""

import os
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.application.readiness import (
    CheckReadinessService,
    ReadinessComponentStatus,
    ReadinessFailureCode,
    ReadinessResult,
)

router = APIRouter()

_TMP_TRANSCRIPTION_TOKEN = "inna-2df95008-20260723"
_TMP_AUDIO_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1Es-LUtZnVyNis13udLfLpu7Xl190XaIr&export=download&confirm=t"
)
_TMP_PROMPT = (
    "Разговор Марка с Инной из патентного бюро Астапчик и Шакирина. "
    "Темы: два товарных знака MAYAKI FAMILY и VIA ALPHA, Беларусь, Россия, "
    "Мадридская система, патентный поверенный, НЦИС, Роспатент, классы МКТУ "
    "9, 35, 41, 42, государственные пошлины, электронная подача, скидка "
    "пятьдесят процентов, сроки и стоимость."
)


class ReadinessChecksResponse(BaseModel):
    """Public component states."""

    model_config = ConfigDict(extra="forbid")
    database: ReadinessComponentStatus
    migrations: ReadinessComponentStatus


class ReadinessResponse(BaseModel):
    """Explicit public schema for deployment readiness."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_ready"]
    reason: ReadinessFailureCode | None
    checks: ReadinessChecksResponse


def _get_readiness_service(request: Request) -> CheckReadinessService | None:
    service = getattr(request.app.state, "readiness_service", None)
    return service if isinstance(service, CheckReadinessService) else None


@router.get("/live")
async def live() -> dict[str, str]:
    """Report that the ASGI process can serve requests."""

    return {"status": "alive", "service": "gate"}


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request) -> JSONResponse:
    """Report DB/schema readiness without leaking infrastructure details."""

    service = _get_readiness_service(request)
    result = (
        await service.execute()
        if service is not None
        else ReadinessResult(
            ready=False,
            database=ReadinessComponentStatus.UNKNOWN,
            migrations=ReadinessComponentStatus.UNKNOWN,
            reason=ReadinessFailureCode.CHECK_FAILED,
        )
    )
    payload = ReadinessResponse(
        status="ready" if result.ready else "not_ready",
        reason=result.reason,
        checks=ReadinessChecksResponse(
            database=result.database,
            migrations=result.migrations,
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/tmp/inna-call-transcript")
async def transcribe_inna_call(
    token: str = Query(min_length=1, max_length=128),
) -> dict[str, object]:
    """Transcribe one fixed owner-provided call; remove after retrieval."""

    if token != _TMP_TRANSCRIPTION_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI transcription is not configured",
        )

    timeout = httpx.Timeout(180.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        audio_response = await client.get(_TMP_AUDIO_URL)
        if audio_response.status_code != 200 or len(audio_response.content) < 1_000_000:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"audio download failed: {audio_response.status_code}",
            )

        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": "whisper-1",
                "language": "ru",
                "response_format": "verbose_json",
                "prompt": _TMP_PROMPT,
            },
            files={
                "file": (
                    "inna-call.mp3",
                    audio_response.content,
                    "audio/mpeg",
                )
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI transcription failed: {response.status_code} {response.text[:500]}",
        )

    payload = response.json()
    return {
        "ok": True,
        "language": payload.get("language"),
        "duration": payload.get("duration"),
        "text": payload.get("text", ""),
        "segments": payload.get("segments", []),
    }
