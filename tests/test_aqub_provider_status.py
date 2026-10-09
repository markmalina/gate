"""Frozen provider status is absent even with legacy configuration."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_frozen_provider_status_is_absent_and_secret_free() -> None:
    settings = Settings(
        _env_file=None,
        ADMIN_BEARER_TOKEN="synthetic-admin-token",
        TELEGRAM_AQUB_BOT_TOKEN="synthetic-secret-token",
        TELEGRAM_AQUB_WEBHOOK_SECRET="synthetic-secret-webhook",
    )
    with patch("app.main.get_settings", return_value=settings):
        app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/admin/ops/aqub-provider",
            headers={"Authorization": "Bearer synthetic-admin-token"},
        )
    assert response.status_code == 404
    assert "synthetic-secret" not in response.text
