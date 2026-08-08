"""AQUB runtime transport reuses the Gate process-owned HTTP client."""

from __future__ import annotations

import httpx
import pytest
from app.application.aqub_live import HttpAQUBRuntimeClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_runtime_transport_reuses_supplied_http_client_for_multiple_posts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"kind": "status"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as shared:
        runtime = HttpAQUBRuntimeClient(
            base_url="https://aqub.example",
            http_client=shared,
        )
        first = await runtime.post_envelope(raw_body=b"{}", signature="sig-1")
        second = await runtime.post_envelope(raw_body=b"{}", signature="sig-2")
        assert shared.is_closed is False

    assert shared.is_closed is True
    assert first["ok"] is True
    assert second["ok"] is True
    assert [request.url.path for request in requests] == ["/gate/inbound", "/gate/inbound"]
    assert [request.headers["x-aqub-signature"] for request in requests] == [
        "sig-1",
        "sig-2",
    ]
