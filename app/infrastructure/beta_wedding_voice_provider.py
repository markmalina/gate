"""Bounded Telegram voice download and one-use OpenAI transcription adapters."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx

_MAX_VOICE_BYTES = 20 * 1024 * 1024


class BetaWeddingVoiceProviderError(RuntimeError):
    pass


class TelegramVoiceDownloadAdapter:
    """Private adapter around the established Beta Telegram client transport."""

    def __init__(
        self, *, credential: str, api_base_url: str, client: httpx.AsyncClient
    ) -> None:
        if not credential or not api_base_url.startswith("https://"):
            raise BetaWeddingVoiceProviderError("voice_telegram_configuration_invalid")
        self._credential = credential
        self._api_base_url = api_base_url.rstrip("/")
        self._client = client

    async def download_voice(self, *, file_id: str) -> bytes:
        if not file_id or len(file_id) > 255:
            raise BetaWeddingVoiceProviderError("voice_file_id_invalid")
        response = await self._client.post(
            f"{self._api_base_url}/bot{self._credential}/getFile",
            json={"file_id": file_id},
        )
        payload = _object(response)
        result = payload.get("result")
        path = result.get("file_path") if isinstance(result, dict) else None
        size = result.get("file_size") if isinstance(result, dict) else None
        if not isinstance(path, str) or not path or ".." in path.split("/"):
            raise BetaWeddingVoiceProviderError("voice_file_path_invalid")
        if type(size) is not int or size < 1 or size > _MAX_VOICE_BYTES:
            raise BetaWeddingVoiceProviderError("voice_file_size_invalid")
        download = await self._client.get(
            f"{self._api_base_url}/file/bot{self._credential}/{path}",
            timeout=30,
        )
        download.raise_for_status()
        if not download.content or len(download.content) > _MAX_VOICE_BYTES:
            raise BetaWeddingVoiceProviderError("voice_download_size_invalid")
        return bytes(download.content)


class OpenAIBetaWeddingVoiceTranscriber:
    """One audio/transcriptions call; raw audio is not persisted or logged."""

    def __init__(
        self, *, api_key: str, api_base_url: str, client: httpx.AsyncClient
    ) -> None:
        if not api_key or api_base_url.rstrip("/") != "https://api.openai.com/v1":
            raise BetaWeddingVoiceProviderError(
                "voice_transcription_configuration_invalid"
            )
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._client = client

    async def transcribe(self, *, audio: bytes) -> str:
        if not audio or len(audio) > _MAX_VOICE_BYTES:
            raise BetaWeddingVoiceProviderError("voice_transcription_audio_invalid")
        response = await self._client.post(
            f"{self._api_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            files={"file": ("voice.ogg", audio, "audio/ogg")},
            data={
                "model": "gpt-4o-mini-transcribe",
                "language": "ru",
                "response_format": "json",
            },
            timeout=60,
        )
        payload = _object(response)
        transcript = payload.get("text")
        if not isinstance(transcript, str) or not transcript.strip():
            raise BetaWeddingVoiceProviderError("voice_transcription_contract_invalid")
        return transcript.strip()


class OpenAIBetaWeddingVoiceStructurer:
    def __init__(
        self, *, api_key: str, api_base_url: str, model: str, client: httpx.AsyncClient
    ) -> None:
        if (
            not api_key
            or api_base_url.rstrip("/") != "https://api.openai.com/v1"
            or not model
        ):
            raise BetaWeddingVoiceProviderError(
                "voice_structurer_configuration_invalid"
            )
        self._api_key, self._api_base_url, self._model, self._client = (
            api_key,
            api_base_url.rstrip("/"),
            model,
            client,
        )

    async def structure(self, *, transcript: str) -> str:
        response = await self._client.post(
            f"{self._api_base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "store": False,
                "tools": [],
                "max_output_tokens": 512,
                "instructions": (
                    "Ты Бета, помощница свадьбы в Минске. Верни по-русски ровно: "
                    "«Я поняла так:» и четыре строки Факты, Решения, Задачи, "
                    "Вопросы. Не добавляй фактов."
                ),
                "input": "Расшифровка голосового сообщения:\n" + transcript,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "beta_wedding_voice_structure",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 4096,
                                }
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            timeout=30,
        )
        payload = _object(response)
        raw = payload.get("output_text")
        if not isinstance(raw, str):
            raise BetaWeddingVoiceProviderError("voice_structurer_output_missing")
        try:
            text = json.loads(raw).get("text")
        except (TypeError, json.JSONDecodeError) as exc:
            raise BetaWeddingVoiceProviderError(
                "voice_structurer_contract_invalid"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise BetaWeddingVoiceProviderError("voice_structurer_contract_invalid")
        return text.strip()


def _object(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    try:
        value = response.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BetaWeddingVoiceProviderError("voice_provider_response_invalid") from exc
    if not isinstance(value, dict):
        raise BetaWeddingVoiceProviderError("voice_provider_response_invalid")
    return cast(dict[str, Any], value)
