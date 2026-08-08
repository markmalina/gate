"""AQUB permanent provider identity, Privacy Mode, retry and startup guards."""

from __future__ import annotations

import logging

import httpx
import pytest
from app.core.config import Settings
from app.services.telegram_aqub_webhook import (
    AQUB_ALLOWED_UPDATES,
    AQUBProviderErrorCode,
    AQUBProviderError,
    AQUBProviderReadinessState,
    build_aqub_public_webhook_url,
    reconcile_aqub_telegram_provider,
    reconcile_aqub_telegram_provider_with_retries,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "TELEGRAM_AQUB_BOT_TOKEN": "aqub-secret-token",
        "TELEGRAM_AQUB_WEBHOOK_SECRET": "aqub-secret-webhook",
        "GATE_AQUB_RECEIPT_SIGNING_KEY": "0123456789abcdef0123456789abcdef",
    }
    values.update(changes)
    return Settings(**values)


def _identity(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "id": 999_999,
        "is_bot": True,
        "username": "AQubAgentBot",
        "can_join_groups": True,
        "can_read_all_group_messages": True,
    }
    result.update(changes)
    return {"ok": True, "result": result}


def test_public_url_uses_existing_precedence_and_exact_owned_path() -> None:
    assert build_aqub_public_webhook_url(
        {
            "PUBLIC_BASE_URL": "https://gate.example/",
            "RAILWAY_PUBLIC_DOMAIN": "ignored.example",
        }
    ) == "https://gate.example/telegram/aqub/preflight"
    assert build_aqub_public_webhook_url({}) is None


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://gate.example",
        "https://localhost",
        "https://user:password@gate.example",
        "https://bad host.example",
        "https://gate.example/base",
        "https://127.0.0.1/",
        "https://10.0.0.1/",
    ],
)
def test_invalid_public_urls_are_rejected(public_base_url: str) -> None:
    assert build_aqub_public_webhook_url({"PUBLIC_BASE_URL": public_base_url}) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "settings",
    [
        _settings(TELEGRAM_AQUB_BOT_TOKEN=""),
        _settings(TELEGRAM_AQUB_WEBHOOK_SECRET=""),
        _settings(GATE_AQUB_RECEIPT_SIGNING_KEY="short"),
    ],
)
async def test_missing_permanent_configuration_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider must not be called")

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await reconcile_aqub_telegram_provider(settings, http_client, state)

    assert result == "skipped_configuration_incomplete"
    assert calls == []
    assert state.snapshot().enabled is True
    assert state.snapshot().configured is False
    assert state.snapshot().error_code is AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE


@pytest.mark.anyio
async def test_missing_public_url_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        monkeypatch.delenv(name, raising=False)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider must not be called")

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await reconcile_aqub_telegram_provider(_settings(), http_client, state)

    assert result == "skipped_configuration_incomplete"
    assert calls == []
    assert state.snapshot().configured is False


@pytest.mark.anyio
async def test_get_me_precedes_exact_webhook_and_no_send_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example/")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json=_identity())
        if request.url.path.endswith("/setWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        raise AssertionError("unexpected Telegram method")

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await reconcile_aqub_telegram_provider(_settings(), http_client, state)

    assert result == "updated"
    assert [call.url.path.rsplit("/", 1)[-1] for call in calls] == ["getMe", "setWebhook"]
    assert not any("sendMessage" in call.url.path for call in calls)
    set_payload = calls[1].read().decode()
    assert "https://gate.example/telegram/aqub/preflight" in set_payload
    assert "aqub-secret-webhook" in set_payload
    assert all(update in set_payload for update in AQUB_ALLOWED_UPDATES)
    assert state.snapshot().enabled is True
    assert state.snapshot().ready is True
    assert state.snapshot().privacy_mode_enabled is False
    assert state.snapshot().error_code is None
    assert "aqub-secret-token" not in repr(state.snapshot())
    assert "aqub-secret-webhook" not in repr(state.snapshot())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("identity", "error_code"),
    [
        (_identity(username="WrongBot"), AQUBProviderErrorCode.IDENTITY_MISMATCH),
        (_identity(is_bot=False), AQUBProviderErrorCode.IDENTITY_MISMATCH),
        (_identity(can_join_groups=False), AQUBProviderErrorCode.GROUP_JOIN_DISABLED),
        (
            _identity(can_read_all_group_messages=False),
            AQUBProviderErrorCode.GROUP_MESSAGE_READ_DISABLED,
        ),
    ],
)
async def test_identity_or_privacy_mismatch_fails_before_set_webhook(
    monkeypatch: pytest.MonkeyPatch,
    identity: dict[str, object],
    error_code: AQUBProviderErrorCode,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=identity)

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(AQUBProviderError) as raised:
            await reconcile_aqub_telegram_provider(_settings(), http_client, state)

    assert raised.value.code is error_code
    assert len(calls) == 1
    assert calls[0].url.path.endswith("/getMe")
    assert state.snapshot().error_code is error_code
    assert state.snapshot().webhook_configured is False


@pytest.mark.anyio
async def test_retries_are_bounded_and_logs_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            401,
            json={"ok": False, "description": "aqub-secret-token leaked detail"},
        )

    state = AQUBProviderReadinessState()
    caplog.set_level(logging.WARNING)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await reconcile_aqub_telegram_provider_with_retries(
            _settings(),
            http_client,
            state,
            retry_delays=(0, 0, 0),
        )

    assert len(calls) == 3
    assert state.snapshot().error_code is AQUBProviderErrorCode.PROVIDER_REQUEST_FAILED
    assert "provider_request_failed" in caplog.text
    assert "aqub-secret-token" not in caplog.text
    assert "leaked detail" not in caplog.text


@pytest.mark.anyio
async def test_startup_reconciliation_includes_aqub_without_replacing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    calls: list[str] = []

    def stub(name: str):
        async def reconcile(*_args: object) -> None:
            calls.append(name)

        return reconcile

    monkeypatch.setattr(main, "reconcile_telegram_webhook_with_retries", stub("gate"))
    monkeypatch.setattr(main, "reconcile_alpha_telegram_webhook_with_retries", stub("alpha"))
    monkeypatch.setattr(main, "reconcile_beta_telegram_webhook_with_retries", stub("beta"))
    monkeypatch.setattr(main, "reconcile_become_telegram_webhook_with_retries", stub("become"))
    monkeypatch.setattr(main, "reconcile_aqub_telegram_provider_with_retries", stub("aqub"))
    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        await main._reconcile_all_telegram_webhooks(_settings(), http_client, state)

    assert calls == ["gate", "alpha", "beta", "become", "aqub"]
