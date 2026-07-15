"""The Alpha webhook is credential- and update-isolated from Gate M."""

import asyncio

from app.core.config import Settings
from app.services.telegram_alpha_webhook import (
    ALPHA_ALLOWED_UPDATES,
    reconcile_alpha_telegram_webhook,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class RecordingHttpClient:
    def __init__(self) -> None:
        self.calls = []

    async def post(self, url: str, *, json: dict):
        self.calls.append((url, json))
        if url.endswith("/getWebhookInfo"):
            return FakeResponse({"ok": True, "result": {"url": ""}})
        return FakeResponse({"ok": True, "result": True})


def test_disabled_connector_never_calls_telegram() -> None:
    client = RecordingHttpClient()
    result = asyncio.run(
        reconcile_alpha_telegram_webhook(Settings(_env_file=None), client)
    )
    assert result == "skipped_connector_disabled"
    assert client.calls == []


def test_reconciliation_uses_only_alpha_secret_path_and_allowed_updates(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example")
    settings = Settings(
        _env_file=None,
        GATE_ALPHA_CONNECTOR_ENABLED=True,
        TELEGRAM_ALPHA_BOT_USERNAME="AlphaViaBot",
        TELEGRAM_ALPHA_BOT_TOKEN="alpha-token",
        TELEGRAM_ALPHA_WEBHOOK_SECRET="alpha-secret",
    )
    client = RecordingHttpClient()

    result = asyncio.run(reconcile_alpha_telegram_webhook(settings, client))

    assert result == "updated"
    assert client.calls[0][0].endswith("/botalpha-token/getWebhookInfo")
    set_url, payload = client.calls[1]
    assert set_url.endswith("/botalpha-token/setWebhook")
    assert payload == {
        "url": "https://gate.example/telegram/alpha/webhook",
        "secret_token": "alpha-secret",
        "drop_pending_updates": False,
        "allowed_updates": ALPHA_ALLOWED_UPDATES,
    }
    assert payload["allowed_updates"] == ["message", "my_chat_member"]
