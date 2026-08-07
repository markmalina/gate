"""Owner-only AQUB provider status exposes only booleans and safe codes."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request

from app.api.health_routes import aqub_provider_health
from app.core.config import Settings
from app.services.telegram_aqub_webhook import (
    AQUBProviderErrorCode,
    AQUBProviderReadinessSnapshot,
    AQUBProviderReadinessState,
)


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/admin/ops/aqub-provider",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )


@pytest.mark.anyio
async def test_status_is_bounded_and_secret_free() -> None:
    state = AQUBProviderReadinessState(enabled=True)
    state.set(
        AQUBProviderReadinessSnapshot(
            enabled=True,
            configured=True,
            identity_verified=True,
            can_join_groups=True,
            privacy_mode_enabled=False,
            webhook_configured=False,
            ready=False,
            error_code=AQUBProviderErrorCode.PRIVACY_MODE_DISABLED,
        )
    )
    app = FastAPI()
    app.state.aqub_provider_readiness = state
    response = await aqub_provider_health(
        _request(app),
        authorization="Bearer synthetic-admin-token",
        settings=Settings(
            _env_file=None,
            ADMIN_BEARER_TOKEN="synthetic-admin-token",
            TELEGRAM_AQUB_BOT_TOKEN="secret-token",
            TELEGRAM_AQUB_WEBHOOK_SECRET="secret-webhook",
        ),
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "enabled": True,
        "configured": True,
        "identity_verified": True,
        "can_join_groups": True,
        "privacy_mode_enabled": False,
        "webhook_configured": False,
        "ready": False,
        "error_code": "privacy_mode_disabled",
    }
    assert "secret-token" not in response.body.decode()
    assert "secret-webhook" not in response.body.decode()
