# ruff: noqa: I001,E501
"""Telegram webhook reconciliation tests."""

import pytest

from app.core.config import Settings
from app.services.telegram_webhook import (
    TELEGRAM_ALLOWED_UPDATES,
    reconcile_telegram_webhook,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    calls: list[dict]
    get_webhook_payload: dict

    def __init__(self, *args, **kwargs) -> None:
        self.calls = _FakeAsyncClient.calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        if url.endswith("/getWebhookInfo"):
            return _FakeResponse(_FakeAsyncClient.get_webhook_payload)
        return _FakeResponse({"ok": True, "result": True})


@pytest.mark.anyio
async def test_reconcile_updates_webhook_when_business_updates_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.get_webhook_payload = {
        "ok": True,
        "result": {
            "url": "https://gate.example/telegram/webhook",
            "allowed_updates": ["message", "callback_query"],
        },
    }

    result = await reconcile_telegram_webhook(
        Settings(TELEGRAM_BOT_TOKEN="token"),
        http_client=_FakeAsyncClient(),
    )

    assert result == "updated"
    assert _FakeAsyncClient.calls[-1]["url"].endswith("/setWebhook")
    assert (
        _FakeAsyncClient.calls[-1]["json"]["allowed_updates"]
        == TELEGRAM_ALLOWED_UPDATES
    )


@pytest.mark.anyio
async def test_reconcile_skips_when_webhook_and_allowed_updates_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.get_webhook_payload = {
        "ok": True,
        "result": {
            "url": "https://gate.example/telegram/webhook",
            "allowed_updates": list(reversed(TELEGRAM_ALLOWED_UPDATES)),
        },
    }

    result = await reconcile_telegram_webhook(
        Settings(TELEGRAM_BOT_TOKEN="token"),
        http_client=_FakeAsyncClient(),
    )

    assert result == "already_current"
    assert len(_FakeAsyncClient.calls) == 1
    assert _FakeAsyncClient.calls[0]["url"].endswith("/getWebhookInfo")
