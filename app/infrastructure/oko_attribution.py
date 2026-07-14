"""HTTP adapter for Oko Control Telegram-start attribution."""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.application.oko_attribution import OkoTelegramStart


class OkoAttributionError(RuntimeError):
    """Stable redacted failure raised when Oko Control rejects attribution."""


class HttpOkoAttributionClient:
    """Post the minimum attribution payload without logging identities or tokens."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_secret: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._url = urljoin(base_url.rstrip("/") + "/", "v1/gate/telegram-start")
        self._bearer_secret = bearer_secret
        self._client = client

    async def record_telegram_start(self, start: OkoTelegramStart) -> None:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._bearer_secret}"},
                json={
                    "tracking_token": start.tracking_token,
                    "telegram_user_id": start.telegram_user_id,
                    "telegram_username": start.telegram_username,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OkoAttributionError("oko_attribution_request_failed") from exc
