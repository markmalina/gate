"""Authenticated Gate to MAYAKI delivery for normalized Instagram events."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.instagram.config import InstagramRuntimeConfig
from app.instagram.events import InstagramEvent


class MayakiInstagramSinkError(RuntimeError):
    """Safe delivery failure; Meta should retry the original webhook."""


def sign_mayaki_intake(
    *, method: str, path: str, timestamp: str, raw_body: str, secret: str
) -> str:
    canonical = f"{method.upper()}\n{path}\n{timestamp}\n{raw_body}"
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


class MayakiInstagramEventSink:
    def __init__(
        self,
        *,
        config: InstagramRuntimeConfig,
        http: httpx.AsyncClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.http = http
        self.now = now

    async def deliver(self, events: list[InstagramEvent]) -> int:
        if not self.config.webhook_delivery_ready:
            raise MayakiInstagramSinkError("instagram_webhook_delivery_not_configured")
        delivered = 0
        for event in events:
            await self._deliver_one(event)
            delivered += 1
        return delivered

    async def _deliver_one(self, event: InstagramEvent) -> None:
        payload = event.mayaki_gate_intake_payload()
        raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timestamp = self.now().isoformat()
        url = self.config.mayaki_intake_url
        path = httpx.URL(url).path
        signature = sign_mayaki_intake(
            method="POST",
            path=path,
            timestamp=timestamp,
            raw_body=raw_body,
            secret=self.config.mayaki_intake_hmac_secret,
        )
        try:
            response = await self.http.post(
                url,
                content=raw_body.encode(),
                headers={
                    "authorization": (f"Bearer {self.config.mayaki_intake_bearer_token}"),
                    "content-type": "application/json",
                    "x-mayaki-timestamp": timestamp,
                    "x-mayaki-signature": signature,
                    "x-mayaki-idempotency-key": event.deduplication_key,
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MayakiInstagramSinkError("mayaki_instagram_intake_unavailable") from exc
