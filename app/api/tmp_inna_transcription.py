"""One-time private transcription endpoint. Remove after retrieving the result."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(include_in_schema=False)

_TOKEN = "inna-0c25f9d9-2aa9-4f6b-a5c5-3d4e8a61f8b7"
_AUDIO_URL = (
    "https://drive.usercontent.google.com/download?"
    "id=1Es-LUtZnVyNis13udLfLpu7Xl190XaIr&export=download&confirm=t"
)


@router.get("/ops/tmp-transcribe-inna")
async def transcribe_inna_call(token: str = Query(...)) -> JSONResponse:
    """Download the owner's temporary call recording and transcribe it once."""

    if token != _TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI is not configured")

    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        audio_response = await client.get(_AUDIO_URL)
        audio_response.raise_for_status()
        if len(audio_response.content) < 1_000_000:
            raise HTTPException(status_code=502, detail="Audio download was incomplete")

        transcription_response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={
                "file": (
                    "inna-call.mp3",
                    audio_response.content,
                    "audio/mpeg",
                )
            },
            data={
                "model": "whisper-1",
                "language": "ru",
                "response_format": "verbose_json",
                "prompt": (
                    "Разговор Марка с Инной из патентного бюро Астапчик и Шакирина. "
                    "Темы: два товарных знака MAYAKI FAMILY и VIA ALPHA, Беларусь, "
                    "Россия, Мадридская система, патентный поверенный, НЦИС, "
                    "Роспатент, классы МКТУ 9, 35, 41, 42, государственные пошлины, "
                    "электронная подача, скидка пятьдесят процентов, сроки и стоимость."
                ),
            },
        )

    try:
        payload = transcription_response.json()
    except ValueError:
        payload = {"error": transcription_response.text[:2000]}
    return JSONResponse(status_code=transcription_response.status_code, content=payload)
