"""MAYAKI intake client used by Gate transport adapters."""

from dataclasses import dataclass
from typing import Any

import httpx

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class MayakiForwardResult:
    """Result of forwarding a normalized inbound event to MAYAKI."""

    status: str
    status_code: int | None = None
    response: JsonObject | None = None
    error_type: str | None = None
    error_message: str | None = None


class MayakiIntakeClient:
    """Forward normalized Gate inbound events to MAYAKI runtime intake."""

    def __init__(
        self,
        *,
        intake_url: str,
        bearer_token: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        self._intake_url = intake_url.strip()
        self._bearer_token = bearer_token.strip()
        self._timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        """Return whether forwarding is configured for this runtime."""

        return bool(self._intake_url)

    async def forward_inbound_event(self, *, event: JsonObject) -> MayakiForwardResult:
        """Forward one normalized inbound event to MAYAKI.

        This method does not raise for HTTP or network failures; Gate must preserve
        its own Telegram webhook behavior even when MAYAKI intake is unavailable.
        """

        if not self.is_configured:
            return MayakiForwardResult(status="skipped")

        headers = {"Content-Type": "application/json"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(self._intake_url, json=event, headers=headers)
        except Exception as exc:  # pragma: no cover - exercised through route fake in tests
            return MayakiForwardResult(
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )

        response_body: JsonObject | None
        try:
            response_body = response.json()
        except ValueError:
            response_body = None

        if response.status_code >= 400:
            return MayakiForwardResult(
                status="failed",
                status_code=response.status_code,
                response=response_body,
            )

        return MayakiForwardResult(
            status="sent",
            status_code=response.status_code,
            response=response_body,
        )
