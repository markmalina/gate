"""Health endpoint tests."""

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["default_mode"] == "manual"


@pytest.mark.anyio
async def test_deployment_fingerprint_omits_secrets() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/deployment/fingerprint")

    assert response.status_code == 200
    body = response.json()
    assert body["bot_username"] == "gate_m_bot"
    assert "telegram_bot_token" not in body
