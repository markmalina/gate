"""Tests for the one-call, redacted Alpha webhook diagnostic."""

from __future__ import annotations

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure.telegram_webhook_status import (
    TelegramWebhookStatusError,
    get_alpha_webhook_status,
    get_beta_provider_probe,
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

def _beta_settings() -> Settings:
    return Settings(
        _env_file=None,
        TELEGRAM_ALPHA_BOT_TOKEN="alpha-secret-token",
        TELEGRAM_BETA_BOT_TOKEN="beta-secret-token",
        TELEGRAM_BETA_BOT_USERNAME="BetaViaBot",
        TELEGRAM_BECOME_BOT_TOKEN="become-secret-token",
        MCP_PUBLIC_RESOURCE_URL="https://gate.example/mcp",
    )


@pytest.mark.anyio
async def test_beta_probe_uses_only_beta_read_methods_and_redacts_provider_data() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getWebhookInfo"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "url": "https://gate.example/telegram/beta/webhook",
                        "pending_update_count": 3,
                        "max_connections": 40,
                        "allowed_updates": ["message", "my_chat_member"],
                        "has_custom_certificate": False,
                        "last_error_date": 1_785_913_200,
                        "last_error_message": (
                            "provider detail with chat 123 and beta-secret-token"
                        ),
                    },
                },
            )
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 700_000_001,
                        "is_bot": True,
                        "username": "BetaViaBot",
                        "can_join_groups": True,
                        "can_read_all_group_messages": True,
                    },
                },
            )
        raise AssertionError("unexpected Telegram method")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await get_beta_provider_probe(
            settings=_beta_settings(),
            http_client=http_client,
        )

    assert len(calls) == 2
    assert {call.method for call in calls} == {"GET"}
    assert {
        call.url.path.rsplit("/", 1)[-1] for call in calls
    } == {"getWebhookInfo", "getMe"}
    assert all("beta-secret-token" in call.url.path for call in calls)
    assert all("alpha-secret-token" not in call.url.path for call in calls)
    assert all("become-secret-token" not in call.url.path for call in calls)
    assert not any(
        mutation in call.url.path
        for call in calls
        for mutation in ("setWebhook", "deleteWebhook", "sendMessage")
    )
    assert result["webhook"] == {
        "configured": True,
        "exact_match": True,
        "actual_origin_match": True,
        "url_host": "gate.example",
        "url_path": "/telegram/beta/webhook",
        "expected_path": "/telegram/beta/webhook",
        "allowed_updates": ["message", "my_chat_member"],
        "pending_update_count": 3,
        "max_connections": 40,
        "has_custom_certificate": False,
        "last_error": {
            "present": True,
            "date": 1_785_913_200,
            "message": "provider_error_redacted",
        },
    }
    assert result["identity"] == {
        "username_match": True,
        "can_join_groups": True,
        "can_read_all_group_messages": True,
        "privacy_mode_enabled": False,
        "ordinary_group_messages_available": True,
    }
    assert result["call_surface"] == {
        "read_methods": ["getWebhookInfo", "getMe"],
        "mutations_allowed": False,
    }
    assert result["provider_calls"] == 2
    assert result["provider_identifiers_exposed"] is False
    assert result["telegram_private_data_exposed"] is False
    assert result["secrets_exposed"] is False
    assert "700000001" not in repr(result)
    assert "beta-secret-token" not in repr(result)
    assert "provider detail" not in repr(result)


@pytest.mark.anyio
async def test_beta_probe_failure_is_closed_and_secret_safe() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            401,
            json={
                "ok": False,
                "description": "beta-secret-token provider failure",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(TelegramWebhookStatusError) as exc_info:
            await get_beta_provider_probe(
                settings=_beta_settings(),
                http_client=http_client,
            )

    assert len(calls) == 1
    assert exc_info.value.code == "beta_probe_provider_read_failed"
    assert "beta-secret-token" not in str(exc_info.value)
    assert "provider failure" not in str(exc_info.value)


@pytest.mark.anyio
async def test_beta_probe_rejects_non_bot_identity_without_partial_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getWebhookInfo"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"url": ""}},
            )
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 7, "is_bot": False}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(TelegramWebhookStatusError) as exc_info:
            await get_beta_provider_probe(
                settings=_beta_settings(),
                http_client=http_client,
            )

    assert exc_info.value.code == "beta_probe_identity_invalid"


@pytest.mark.anyio
async def test_beta_probe_missing_beta_credential_makes_no_provider_call() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider must not be called")

    settings = _beta_settings().model_copy(
        update={"telegram_beta_bot_token": _settings().telegram_alpha_bot_token.__class__("")}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(TelegramWebhookStatusError) as exc_info:
            await get_beta_provider_probe(
                settings=settings,
                http_client=http_client,
            )

    assert exc_info.value.code == "beta_probe_credentials_incomplete"
    assert calls == []

