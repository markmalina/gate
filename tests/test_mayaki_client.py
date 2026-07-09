"""MAYAKI intake client tests."""

from typing import Any

import httpx
import pytest

from app.services.mayaki import MayakiIntakeClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class CaptureTransport(httpx.AsyncBaseTransport):
    """HTTPX transport fake that captures outbound requests."""

    def __init__(self, status_code: int = 200, json_body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.json_body = json_body or {"ok": True}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.json_body, request=request)


@pytest.mark.anyio
async def test_mayaki_forwarder_skips_when_not_configured() -> None:
    client = MayakiIntakeClient(intake_url="")

    result = await client.forward_inbound_event(event={"message_text": "оковозрождение"})

    assert result.status == "skipped"
    assert result.status_code is None


@pytest.mark.anyio
async def test_mayaki_forwarder_sends_bearer_request(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = CaptureTransport(json_body={"status": "mayak_detected"})
    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 5.0
            self._client = original_async_client(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self._client

        async def __aexit__(self, *_args: object) -> None:
            await self._client.aclose()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = MayakiIntakeClient(
        intake_url="https://mayaki.example/intake/gate",
        bearer_token="secret-token",
    )

    result = await client.forward_inbound_event(event={"message_text": "оковозрождение"})

    assert result.status == "sent"
    assert result.status_code == 200
    assert result.response == {"status": "mayak_detected"}
    assert len(transport.requests) == 1
    assert transport.requests[0].headers["Authorization"] == "Bearer secret-token"


@pytest.mark.anyio
async def test_mayaki_forwarder_reports_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = CaptureTransport(status_code=500, json_body={"error": "boom"})
    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self._client = original_async_client(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self._client

        async def __aexit__(self, *_args: object) -> None:
            await self._client.aclose()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = MayakiIntakeClient(intake_url="https://mayaki.example/intake/gate")

    result = await client.forward_inbound_event(event={"message_text": "оковозрождение"})

    assert result.status == "failed"
    assert result.status_code == 500
    assert result.response == {"error": "boom"}
