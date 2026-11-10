"""Source-enforced AQUB freeze preserves Gate without outbound AQUB effects."""

from pathlib import Path

import httpx
import pytest
from app.application.aqub_live import AQUBRuntimeUnavailable, HttpAQUBRuntimeClient
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_legacy_client_refuses_before_network_even_with_configured_url() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("Frozen AQUB must not receive a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = HttpAQUBRuntimeClient(base_url="https://aqub.example", http_client=client)
        with pytest.raises(AQUBRuntimeUnavailable, match="aqub_project_frozen"):
            await runtime.post_envelope(raw_body=b"{}", signature="synthetic")
    assert calls == []


@pytest.mark.anyio
async def test_aqub_ingress_delivery_and_provider_status_are_not_registered() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
    ) as client:
        for method, path in (
            ("POST", "/telegram/aqub/preflight"),
            ("POST", "/internal/aqub/deliver"),
            ("GET", "/admin/ops/aqub-provider"),
        ):
            response = await client.request(method, path, json={"update_id": 1})
            assert response.status_code == 404


def test_owner_boundary_cannot_be_reactivated_by_legacy_environment_flags() -> None:
    settings = Settings(
        _env_file=None,
        GATE_AQUB_MEDIA_FORWARD_ENABLED=True,
        GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED=True,
        GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED=True,
        TELEGRAM_AQUB_BOT_TOKEN="synthetic-legacy-token",
    )
    # Compatibility settings can still be parsed; active entrypoints never use them.
    assert settings.gate_aqub_media_forward_enabled
    source = Path("app/main.py").read_text()
    assert "aqub" not in source
    assert "AQUB" not in Path("railway.toml").read_text()
    paths = create_app().openapi()["paths"]
    assert "/telegram/webhook" in paths
    assert "/mcp" in paths
    assert not any("aqub" in path for path in paths)
