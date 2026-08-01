"""Contract tests for the isolated BECOME Telegram webhook reconciler."""

import asyncio
import logging

import httpx
import pytest
from app.core.config import Settings
from app.services import telegram_become_webhook
from app.services.telegram_become_webhook import (
    BECOME_ALLOWED_UPDATES,
    become_webhook_failure_details,
    build_become_public_webhook_url,
    reconcile_become_telegram_webhook,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _Telegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, *, json: dict[str, object]) -> _Response:
        self.calls.append((url, json))
        if url.endswith("/getWebhookInfo"):
            return _Response(
                {
                    "ok": True,
                    "result": {
                        "url": "",
                        "allowed_updates": [],
                    },
                }
            )
        return _Response({"ok": True, "result": True})


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "GATE_BECOME_CONNECTOR_ENABLED": True,
        "TELEGRAM_BECOME_BOT_TOKEN": "synthetic-become-token",
        "TELEGRAM_BECOME_BOT_USERNAME": "BECOMEViaBot",
        "TELEGRAM_BECOME_WEBHOOK_SECRET": "synthetic-become-secret",
    }
    values.update(changes)
    return Settings(**values)


def test_become_public_url_is_separate_from_alpha() -> None:
    assert build_become_public_webhook_url({"PUBLIC_BASE_URL": "https://gate.example"}) == (
        "https://gate.example/telegram/become/webhook"
    )


@pytest.mark.anyio
async def test_become_reconciliation_uses_only_become_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    telegram = _Telegram()

    result = await reconcile_become_telegram_webhook(
        _settings(TELEGRAM_ALPHA_BOT_TOKEN="must-not-be-used"),
        telegram,  # type: ignore[arg-type]
    )

    assert result == "updated"
    assert len(telegram.calls) == 2
    assert all("synthetic-become-token" in url for url, _ in telegram.calls)
    assert all("must-not-be-used" not in url for url, _ in telegram.calls)
    set_webhook = telegram.calls[1][1]
    assert set_webhook["url"] == "https://gate.example/telegram/become/webhook"
    assert set_webhook["secret_token"] == "synthetic-become-secret"
    assert set_webhook["allowed_updates"] == BECOME_ALLOWED_UPDATES
    assert set_webhook["drop_pending_updates"] is False


@pytest.mark.anyio
async def test_disabled_become_connector_makes_no_telegram_call() -> None:
    telegram = _Telegram()

    result = await reconcile_become_telegram_webhook(
        _settings(GATE_BECOME_CONNECTOR_ENABLED=False),
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
def test_become_webhook_http_failure_is_redacted(
    status_code: int,
    reason: str,
) -> None:
    request = httpx.Request("POST", "https://api.telegram.org/redacted/getWebhookInfo")
    response = httpx.Response(status_code, request=request)
    exc = httpx.HTTPStatusError("sensitive-body", request=request, response=response)

    expected_reason = "get_webhook_info_rejected" if status_code == 400 else reason
    assert become_webhook_failure_details(exc) == (
        expected_reason,
        status_code,
        "get_webhook_info",
    )


@pytest.mark.anyio
async def test_become_webhook_recovers_after_transient_failure(
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
        telegram_become_webhook,
        "reconcile_become_telegram_webhook",
        _reconcile,
    )
    monkeypatch.setattr(telegram_become_webhook.asyncio, "sleep", _sleep)
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await telegram_become_webhook.reconcile_become_telegram_webhook_with_retries(
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
    assert "synthetic-become-token" not in caplog.text


def test_become_set_webhook_rejection_is_safely_classified() -> None:
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

    assert become_webhook_failure_details(exc) == (
        "webhook_url_invalid",
        400,
        "set_webhook",
    )
