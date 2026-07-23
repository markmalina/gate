"""Temporary one-shot transcription route for the owner-provided Inna call.

Delete immediately after retrieval. This route is token-gated, accepts no user payload,
and fetches one fixed owner-controlled audio object.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter()

_TOKEN = "inna-2df95008-20260723"
_AUDIO_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1Es-LUtZnVyNis13udLfLpu7Xl190XaIr&export=download&confirm=t"
)
_PROMPT = (
    "Разговор Марка с Инной из патентного бюро Астапчик и Шакирина. "
    "Темы: два товарных знака MAYAKI FAMILY и VIA ALPHA, Беларусь, Россия, "
    "Мадридская система, патентный поверенный, НЦИС, Роспатент, классы МКТУ "
    "9, 35, 41, 42, государственные пошлины, электронная подача, скидка "
    "пятьдесят процентов, сроки и стоимость."
)


@router.get("/tmp/inna-call-transcript")
async def transcribe_inna_call(
    token: str = Query(min_length=1, max_length=128),
) -> dict[str, object]:
    if token != _TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI transcription is not configured",
        )

    timeout = httpx.Timeout(180.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        audio_response = await client.get(_AUDIO_URL)
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
                "prompt": _PROMPT,
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
