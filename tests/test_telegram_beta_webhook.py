"""Contract tests for the isolated Beta Telegram webhook reconciler."""

import pytest
from app.core.config import Settings
from app.services.telegram_beta_webhook import (
    BETA_ALLOWED_UPDATES,
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
