"""Contract tests for the isolated Beta Telegram webhook reconciler."""

import asyncio
import logging

import httpx
import pytest
from app.core.config import Settings
from app.services import telegram_beta_webhook
from app.services.telegram_beta_webhook import (
    BETA_ALLOWED_UPDATES,
    beta_webhook_failure_details,
    build_beta_public_webhook_url,
    reconcile_beta_telegram_webhook,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _Telegram:
    def __init__(self, webhook_result: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.webhook_result = webhook_result or {
            "url": "",
            "allowed_updates": [],
        }

    async def post(self, url: str, *, json: dict[str, object]) -> _Response:
        self.calls.append((url, json))
        if url.endswith("/getWebhookInfo"):
            return _Response(
                {
                    "ok": True,
                    "result": self.webhook_result,
                }
            )
        return _Response({"ok": True, "result": True})


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "GATE_BETA_CONNECTOR_ENABLED": True,
        "TELEGRAM_BETA_BOT_TOKEN": "synthetic-beta-token",
        "TELEGRAM_BETA_BOT_USERNAME": "BetaViaBot",
        "TELEGRAM_BETA_WEBHOOK_SECRET": "synthetic-beta-secret",
    }
    values.update(changes)
    return Settings(**values)


def test_beta_public_url_is_separate_from_alpha() -> None:
    assert build_beta_public_webhook_url({"PUBLIC_BASE_URL": "https://gate.example"}) == (
        "https://gate.example/telegram/beta/webhook"
    )


def test_beta_allowed_updates_always_admit_callback_queries() -> None:
    assert BETA_ALLOWED_UPDATES == ["message", "my_chat_member", "callback_query"]


@pytest.mark.anyio
async def test_missing_callback_query_forces_canonical_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    telegram = _Telegram(
        {
            "url": "https://gate.example/telegram/beta/webhook",
            "allowed_updates": ["message", "my_chat_member"],
        }
    )

    result = await reconcile_beta_telegram_webhook(
        _settings(),
        telegram,  # type: ignore[arg-type]
    )

    assert result == "updated"
    assert len(telegram.calls) == 2
    assert telegram.calls[1][1]["allowed_updates"] == BETA_ALLOWED_UPDATES


@pytest.mark.anyio
async def test_beta_reconciliation_uses_only_beta_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    telegram = _Telegram()

    result = await reconcile_beta_telegram_webhook(
        _settings(TELEGRAM_ALPHA_BOT_TOKEN="must-not-be-used"),
        telegram,  # type: ignore[arg-type]
    )

    assert result == "updated"
    assert len(telegram.calls) == 2
    assert all("synthetic-beta-token" in url for url, _ in telegram.calls)
    assert all("must-not-be-used" not in url for url, _ in telegram.calls)
    set_webhook = telegram.calls[1][1]
    assert set_webhook["url"] == "https://gate.example/telegram/beta/webhook"
    assert set_webhook["secret_token"] == "synthetic-beta-secret"
    assert set_webhook["allowed_updates"] == BETA_ALLOWED_UPDATES
    assert set_webhook["drop_pending_updates"] is False


@pytest.mark.anyio
async def test_disabled_beta_connector_makes_no_telegram_call() -> None:
    telegram = _Telegram()

    result = await reconcile_beta_telegram_webhook(
        _settings(GATE_BETA_CONNECTOR_ENABLED=False),
        telegram,  # type: ignore[arg-type]
    )

    assert result == "skipped_connector_disabled"
    assert telegram.calls == []


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (401, "http_authentication_failed"),
        (403, "http_authentication_failed"),
        (429, "http_rate_limited"),
        (500, "http_server_error"),
        (400, "http_client_error"),
    ],
)
def test_beta_webhook_http_failure_is_redacted(
    status_code: int,
    reason: str,
) -> None:
    request = httpx.Request("POST", "https://api.telegram.org/redacted/getWebhookInfo")
    response = httpx.Response(status_code, request=request)
    exc = httpx.HTTPStatusError("sensitive-body", request=request, response=response)

    expected_reason = "get_webhook_info_rejected" if status_code == 400 else reason
    assert beta_webhook_failure_details(exc) == (
        expected_reason,
        status_code,
        "get_webhook_info",
    )


@pytest.mark.anyio
async def test_beta_webhook_recovers_after_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    sleeps: list[int] = []

    async def _reconcile(_settings: Settings, _client: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request(
                "POST",
                "https://api.telegram.org/redacted/getWebhookInfo",
            )
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError(
                "secret-must-not-be-logged",
                request=request,
                response=response,
            )
        return "already_current"

    async def _sleep(seconds: int) -> None:
        sleeps.append(seconds)
        if seconds == 300:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        telegram_beta_webhook,
        "reconcile_beta_telegram_webhook",
        _reconcile,
    )
    monkeypatch.setattr(telegram_beta_webhook.asyncio, "sleep", _sleep)
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await telegram_beta_webhook.reconcile_beta_telegram_webhook_with_retries(
            _settings(),
            object(),  # type: ignore[arg-type]
        )

    assert calls == 2
    assert sleeps == [3, 300]
    assert (
        "operation=get_webhook_info reason=http_authentication_failed "
        "status_code=401"
        in caplog.text
    )
    assert "secret-must-not-be-logged" not in caplog.text
    assert "synthetic-beta-token" not in caplog.text


def test_beta_set_webhook_rejection_is_safely_classified() -> None:
    request = httpx.Request(
        "POST",
        "https://api.telegram.org/redacted/setWebhook",
    )
    response = httpx.Response(
        400,
        request=request,
        json={
            "ok": False,
            "description": "Bad Request: bad webhook: failed to resolve host",
        },
    )
    exc = httpx.HTTPStatusError(
        "must-not-be-logged",
        request=request,
        response=response,
    )

    assert beta_webhook_failure_details(exc) == (
        "webhook_url_invalid",
        400,
        "set_webhook",
    )
