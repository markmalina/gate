"""Telegram webhook reconciliation helper tests."""

import pytest

from app.services.telegram_webhook import (
    _require_telegram_ok,
    build_public_webhook_url,
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
