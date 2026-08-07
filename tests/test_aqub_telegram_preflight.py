"""Integration coverage for the wired default-off AQUB preflight ingress."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.aqub_telegram_preflight import router
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import Settings, get_settings

READY_AT = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)


class DurableStore:
    def __init__(self) -> None:
        self.commands: list[AcceptTelegramReceiptCommand] = []
        self.acceptances: dict[tuple[str, int], TelegramReceiptAcceptance] = {}

    async def create_if_absent(
        self, command: AcceptTelegramReceiptCommand
    ) -> TelegramReceiptAcceptance:
        self.commands.append(command)
        key = (command.bot_key, command.update_id)
        existing = self.acceptances.get(key)
        if existing is not None:
            return TelegramReceiptAcceptance(
                receipt_id=existing.receipt_id,
                outbox_event_id=existing.outbox_event_id,
                duplicate=True,
            )
        created = TelegramReceiptAcceptance(
            receipt_id="raw-receipt SECRET-RECEIPT",
            outbox_event_id="raw-outbox SECRET-OUTBOX",
            duplicate=False,
        )
        self.acceptances[key] = created
        return created


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "GATE_AQUB_S0_PREFLIGHT_ENABLED": True,
        "TELEGRAM_AQUB_BOT_USERNAME": "@AQubAgentBot",
        "TELEGRAM_AQUB_WEBHOOK_SECRET": "webhook SECRET-WEBHOOK",
        "GATE_AQUB_GROUP_CHAT_ID": "-1007001",
        "GATE_AQUB_ACE_USER_ID": "7002",
        "GATE_AQUB_ALPHA_USER_ID": "7003",
        "GATE_AQUB_S0_READY_AT": READY_AT.isoformat(),
        "GATE_AQUB_RECEIPT_SIGNING_KEY": (
            "0123456789abcdef0123456789abcdef SECRET-SIGNING"
        ),
    }
    values.update(changes)
    return Settings(**values)


def _update(**message_changes: object) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 501,
        "date": int(READY_AT.timestamp()),
        "chat": {"id": -1007001, "type": "supergroup"},
        "from": {"id": 7003, "is_bot": False},
        "text": "/aqub_ping",
    }
    message.update(message_changes)
    return {"update_id": 500, "message": message}


def _client(settings: Settings, store: DurableStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_telegram_receipt_store] = lambda: store
    return TestClient(app)


def _post(client: TestClient, update: dict[str, object]):
    return client.post(
        "/telegram/aqub/preflight",
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "webhook SECRET-WEBHOOK"
        },
        json=update,
    )


def test_enabled_exact_route_owns_once_and_duplicate_reuses_receipt() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        first = _post(client, _update())
        second = _post(client, _update())

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["owned"] is True
    assert first_body["accepted"] is True
    assert first_body["legacy_dispatch"] is False
    assert first_body["canary_dispatch"] is False
    assert first_body["receipt"]["duplicate"] is False
    assert second_body["receipt"]["duplicate"] is True
    assert (
        second_body["receipt"]["command_receipt_ref"]
        == first_body["receipt"]["command_receipt_ref"]
    )
    assert first_body["receipt"]["delivery_status"] == "not_run"
    assert [command.bot_key for command in store.commands] == [
        "telegram_aqub_preflight",
        "telegram_aqub_preflight",
    ]


def test_disabled_or_unbound_route_touches_no_storage() -> None:
    disabled_store = DurableStore()
    with _client(
        _settings(GATE_AQUB_S0_PREFLIGHT_ENABLED=False), disabled_store
    ) as client:
        disabled = _post(client, _update())
    assert disabled.status_code == 503
    assert disabled_store.commands == []

    unbound_store = DurableStore()
    with _client(_settings(), unbound_store) as client:
        unbound = _post(
            client,
            _update(chat={"id": -1007999, "type": "supergroup"}),
        )
    assert unbound.status_code == 200
    assert unbound.json()["owned"] is False
    assert unbound_store.commands == []


def test_http_receipt_redacts_every_raw_identifier_text_and_secret() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update())

    receipt_text = str(response.json()["receipt"])
    for forbidden in (
        "SECRET-RECEIPT",
        "SECRET-OUTBOX",
        "SECRET-WEBHOOK",
        "SECRET-SIGNING",
        "-1007001",
        "7003",
        "/aqub_ping",
    ):
        assert forbidden not in receipt_text
