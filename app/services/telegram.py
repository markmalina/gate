"""Telegram Bot API client for safe outbound messages."""

from typing import Any

import httpx


class TelegramClient:
    """Small async Telegram Bot API client."""

    def __init__(
        self,
        credential: str,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._credential = credential
        self._api_base_url = api_base_url.rstrip("/")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a Telegram text message and return the Telegram API response."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id

        url = f"{self._api_base_url}/bot{self._credential}/sendMessage"
        return await self._post_telegram_json(url=url, payload=payload)

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        business_connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Edit a Telegram text message and return the Telegram API response."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id

        url = f"{self._api_base_url}/bot{self._credential}/editMessageText"
        return await self._post_telegram_json(url=url, payload=payload)

    async def _post_telegram_json(
        self, *, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

        if not isinstance(result, dict):
            raise RuntimeError("Telegram API returned a non-object response")

        return result
