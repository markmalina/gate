"""Telegram webhook reconciliation helper tests."""

import httpx
import pytest
from app.core.config import Settings
from app.services.telegram_webhook import (
    TELEGRAM_ALLOWED_UPDATES,
    _require_telegram_ok,
    build_public_webhook_url,
    reconcile_telegram_webhook,
)


def test_build_webhook_url_prefers_explicit_public_base_url() -> None:
    url = build_public_webhook_url(
        {
            "PUBLIC_BASE_URL": "https://gate.example/",
            "RAILWAY_PUBLIC_DOMAIN": "ignored.up.railway.app",
        }
    )

    assert url == "https://gate.example/telegram/webhook"


def test_build_webhook_url_adds_https_for_railway_domain() -> None:
    url = build_public_webhook_url(
        {"RAILWAY_PUBLIC_DOMAIN": "gate-current.up.railway.app"}
    )

    assert url == "https://gate-current.up.railway.app/telegram/webhook"


def test_build_webhook_url_returns_none_without_public_domain() -> None:
    assert build_public_webhook_url({}) is None


def test_require_telegram_ok_accepts_success_object() -> None:
    payload = {"ok": True, "result": {"url": "https://gate.example/telegram/webhook"}}

    assert _require_telegram_ok(payload) == payload


def test_require_telegram_ok_rejects_failed_or_non_object_response() -> None:
    with pytest.raises(RuntimeError):
        _require_telegram_ok({"ok": False})
    with pytest.raises(RuntimeError):
        _require_telegram_ok([])


@pytest.mark.anyio
async def test_reconciliation_uses_injected_process_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "ok": True,
                "result": {
                    "url": "https://gate.example/telegram/webhook",
                    "allowed_updates": TELEGRAM_ALLOWED_UPDATES,
                },
            },
        )

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    settings = Settings(TELEGRAM_BOT_TOKEN="secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await reconcile_telegram_webhook(settings, http_client)

        assert result == "already_current"
        assert len(requests) == 1
        assert not http_client.is_closed
