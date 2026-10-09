"""Frozen AQUB cannot use the Gate process-owned HTTP client."""

from __future__ import annotations

import httpx
import pytest
from app.application.aqub_live import AQUBRuntimeUnavailable, HttpAQUBRuntimeClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_frozen_runtime_never_posts_or_closes_supplied_http_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"kind": "status"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as shared:
        runtime = HttpAQUBRuntimeClient(
            base_url="https://aqub.example",
            http_client=shared,
        )
        for signature in ("sig-1", "sig-2"):
            with pytest.raises(AQUBRuntimeUnavailable, match="aqub_project_frozen"):
                await runtime.post_envelope(raw_body=b"{}", signature=signature)
        assert shared.is_closed is False

    assert shared.is_closed is True
    assert requests == []
