"""Tests for the one-call, redacted Alpha webhook diagnostic."""

from __future__ import annotations

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure.telegram_webhook_status import (
    TelegramWebhookStatusError,
    get_alpha_webhook_status,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        TELEGRAM_ALPHA_BOT_TOKEN="secret-bot-token",
        TELEGRAM_ALPHA_BOT_USERNAME="AlphaViaBot",
        MCP_PUBLIC_RESOURCE_URL="https://gate.example/mcp",
    )


@pytest.mark.anyio
async def test_get_webhook_status_calls_telegram_once_and_redacts_url() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "url": "https://gate.example/telegram/alpha/webhook",
                    "pending_update_count": 0,
                    "max_connections": 40,
                    "allowed_updates": ["message"],
                    "has_custom_certificate": False,
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await get_alpha_webhook_status(
            settings=_settings(),
            http_client=http_client,
        )

    assert len(calls) == 1
    assert calls[0].url.path.endswith("/getWebhookInfo")
    assert result == {
        "api_ok": True,
        "bot_username": "@AlphaViaBot",
        "webhook_configured": True,
        "exact_match": True,
        "actual_origin_match": True,
        "expected_webhook_path": "/telegram/alpha/webhook",
        "pending_update_count": 0,
        "allowed_updates": ["message"],
        "max_connections": 40,
        "has_custom_certificate": False,
        "last_error_present": False,
        "provider_dialog_ids_exposed": False,
        "telegram_private_data_exposed": False,
    }
    assert "secret-bot-token" not in repr(result)
    assert "url" not in result


@pytest.mark.anyio
async def test_get_webhook_status_returns_only_safe_provider_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"ok": False, "description": "secret provider detail"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(TelegramWebhookStatusError) as exc_info:
            await get_alpha_webhook_status(
                settings=_settings(),
                http_client=http_client,
            )

    assert exc_info.value.code == "alpha_webhook_provider_read_failed"
    assert "secret provider detail" not in str(exc_info.value)
