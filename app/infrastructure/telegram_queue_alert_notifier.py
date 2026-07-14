"""Dormant authenticated HTTP notifier for redacted queue incidents."""

from urllib.parse import urlparse

import httpx

from app.application.telegram_queue_incidents import (
    TelegramQueueAlertNotifier,
    TelegramQueueIncident,
)


class HttpTelegramQueueAlertNotifier(TelegramQueueAlertNotifier):
    """Deliver a redacted incident to one authenticated HTTPS endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_endpoint = endpoint.strip()
        parsed = urlparse(normalized_endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("endpoint must be an absolute HTTPS URL")
        normalized_token = bearer_token.strip()
        if not normalized_token:
            raise ValueError("bearer_token is required")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self._endpoint = normalized_endpoint
        self._bearer_token = normalized_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def notify(self, incident: TelegramQueueIncident) -> None:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Idempotency-Key": incident.incident_id,
        }
        payload: dict[str, object] = {
            "schema_version": "gate.queue_incident.v1",
            "incident_id": incident.incident_id,
            "fingerprint": incident.fingerprint,
            "level": incident.level.value,
            "reasons": list(incident.reasons),
            "observed_at": incident.observed_at.isoformat(),
            "status": incident.status.value,
        }
        if self._client is not None:
            await self._post(self._client, headers=headers, payload=payload)
            return

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            await self._post(client, headers=headers, payload=payload)

    async def _post(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> None:
        response = await client.post(
            self._endpoint,
            headers=headers,
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
