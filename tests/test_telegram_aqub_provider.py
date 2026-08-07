"""AQUB provider preflight identity, Privacy Mode, retry and startup guards."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
from app.core.config import Settings
from app.services.telegram_aqub_webhook import (
    AQUB_ALLOWED_UPDATES,
    AQUBProviderErrorCode,
    AQUBProviderPreflightError,
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
        "GATE_AQUB_S0_PREFLIGHT_ENABLED": True,
        "TELEGRAM_AQUB_BOT_USERNAME": "@AQubAgentBot",
        "TELEGRAM_AQUB_BOT_TOKEN": "aqub-secret-token",
        "TELEGRAM_AQUB_WEBHOOK_SECRET": "aqub-secret-webhook",
        "GATE_AQUB_GROUP_CHAT_ID": "-1007001",
        "GATE_AQUB_ACE_USER_ID": "7002",
        "GATE_AQUB_ALPHA_USER_ID": "7003",
        "GATE_AQUB_S0_READY_AT": datetime(2026, 8, 7, tzinfo=UTC).isoformat(),
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
        "can_read_all_group_messages": False,
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
        "file:///tmp/gate",
        "https:///missing-host",
        "/relative/path",
        "https://user:password@gate.example",
        "https://bad host.example",
        " https://gate.example",
        "https://gate.example ",
        "https://localhost",
        "https://gate..example",
        "https://gate.example?unsafe=true",
        "https://gate.example#fragment",
        "https://gate.example:99999",
        "https://gate.example/base",
        "https://gate.example/a b",
        "https://gate.example/a%20b",
        "https://gate.example/%2F",
        "https://gate.example//",
        "https://gate.example:/",
        "gate.example:",
        "https://gate.example:not-a-port/",
        "gate.example:not-a-port",
        "https://gate.example:+443/",
        "https://gate.exa\tmple/",
        "https://gate.example:\t443/",
        "gate.example:\t443",
        "https://gate.exam\nple/",
        "https://gate.example:\r443/",
        "https://gate.example/line\nfeed",
        "https://gate.example:\x1f443/",
        "https://gate.exa\x7fmple/",
    ],
)
@pytest.mark.anyio
async def test_invalid_public_url_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    public_base_url: str,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", public_base_url)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider must not be called")

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await reconcile_aqub_telegram_provider(
            _settings(),
            http_client,
            state,
        )

    assert result == "skipped_configuration_incomplete"
    assert calls == []
    assert state.snapshot().configured is False
    assert (
        state.snapshot().error_code
        is AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE
    )


def test_public_origin_with_port_constructs_exact_webhook_path() -> None:
    assert build_aqub_public_webhook_url(
        {"PUBLIC_BASE_URL": "https://gate.example:8443/"}
    ) == "https://gate.example:8443/telegram/aqub/preflight"


def test_nul_control_is_rejected_by_url_builder() -> None:
    # POSIX forbids NUL in an environment value, so exercise the pure boundary.
    assert build_aqub_public_webhook_url(
        {"PUBLIC_BASE_URL": "https://gate.exa\x00mple/"}
    ) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "settings",
    [
        _settings(GATE_AQUB_S0_PREFLIGHT_ENABLED=False),
        _settings(TELEGRAM_AQUB_BOT_TOKEN=""),
        _settings(TELEGRAM_AQUB_WEBHOOK_SECRET=""),
        _settings(TELEGRAM_AQUB_BOT_USERNAME="@WrongBot"),
        _settings(GATE_AQUB_GROUP_CHAT_ID=""),
        _settings(GATE_AQUB_ACE_USER_ID=""),
        _settings(GATE_AQUB_ALPHA_USER_ID=""),
        _settings(GATE_AQUB_ALPHA_USER_ID="7002"),
        _settings(GATE_AQUB_S0_READY_AT=""),
        _settings(GATE_AQUB_S0_READY_AT="not-a-timestamp"),
        _settings(GATE_AQUB_S0_READY_AT="2026-08-07T12:00:00"),
        _settings(GATE_AQUB_RECEIPT_SIGNING_KEY="short"),
    ],
)
async def test_dormant_or_incomplete_configuration_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider must not be called")

    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await reconcile_aqub_telegram_provider(settings, http_client, state)

    assert result.startswith("skipped_")
    assert calls == []
    assert state.snapshot().ready is False
    assert state.snapshot().configured is False
    if settings.gate_aqub_s0_preflight_enabled:
        assert (
            state.snapshot().error_code
            is AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE
        )
    else:
        assert state.snapshot().error_code is None


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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await reconcile_aqub_telegram_provider(
            _settings(),
            http_client,
            state,
        )

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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await reconcile_aqub_telegram_provider(
            _settings(),
            http_client,
            state,
        )

    assert result == "updated"
    assert [call.url.path.rsplit("/", 1)[-1] for call in calls] == [
        "getMe",
        "setWebhook",
    ]
    assert not any("sendMessage" in call.url.path for call in calls)
    set_payload = calls[1].read().decode()
    assert "https://gate.example/telegram/aqub/preflight" in set_payload
    assert "aqub-secret-webhook" in set_payload
    assert all(update in set_payload for update in AQUB_ALLOWED_UPDATES)
    assert state.snapshot().ready is True
    assert state.snapshot().error_code is None
    assert "aqub-secret-token" not in repr(state.snapshot())
    assert "aqub-secret-webhook" not in repr(state.snapshot())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("identity", "error_code"),
    [
        (_identity(username="WrongBot"), AQUBProviderErrorCode.IDENTITY_MISMATCH),
        (_identity(is_bot=False), AQUBProviderErrorCode.IDENTITY_MISMATCH),
        (
            _identity(can_join_groups=False),
            AQUBProviderErrorCode.GROUP_JOIN_DISABLED,
        ),
        (
            _identity(can_read_all_group_messages=True),
            AQUBProviderErrorCode.PRIVACY_MODE_DISABLED,
        ),
        (
            _identity(can_read_all_group_messages=None),
            AQUBProviderErrorCode.PRIVACY_MODE_DISABLED,
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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(AQUBProviderPreflightError) as raised:
            await reconcile_aqub_telegram_provider(
                _settings(),
                http_client,
                state,
            )

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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
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
    monkeypatch.setattr(
        main,
        "reconcile_alpha_telegram_webhook_with_retries",
        stub("alpha"),
    )
    monkeypatch.setattr(
        main,
        "reconcile_beta_telegram_webhook_with_retries",
        stub("beta"),
    )
    monkeypatch.setattr(
        main,
        "reconcile_become_telegram_webhook_with_retries",
        stub("become"),
    )
    monkeypatch.setattr(
        main,
        "reconcile_aqub_telegram_provider_with_retries",
        stub("aqub"),
    )
    state = AQUBProviderReadinessState()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        await main._reconcile_all_telegram_webhooks(
            _settings(),
            http_client,
            state,
        )

    assert calls == ["gate", "alpha", "beta", "become", "aqub"]
