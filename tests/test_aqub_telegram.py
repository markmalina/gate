"""Integration coverage for the permanent AQUB Telegram production ingress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.api.aqub_telegram import (
    get_aqub_transport_store,
    get_aqub_production_binding,
    router,
)
from app.api.telegram_ingress import TELEGRAM_WEBHOOK_CHAIN
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBDurableAcceptance,
    AQUBTransportBinding,
    AQUBTransportCommand,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
)
from app.core.config import Settings, get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

TEST_CHAT_ID = "-1001234567890"
TEST_ALPHA_USER_ID = "123456789"
TEST_READY_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
TEST_SIGNING_KEY = b"0123456789abcdef0123456789abcdef-test"


class DurableStore:
    def __init__(self) -> None:
        self.commands: list[AQUBTransportCommand] = []
        self.acceptances: dict[str, AQUBDurableAcceptance] = {}

    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        key = command.external_id
        existing = self.acceptances.get(key)
        if existing is not None:
            return AQUBDurableAcceptance(
                status=AQUBReceiptStatus.COMMITTED,
                disposition=AQUBStoreDisposition.DUPLICATE,
                command_receipt_id=existing.command_receipt_id,
                root_event_id=existing.root_event_id,
                correlation_id=existing.correlation_id,
                causation_id=existing.causation_id,
            )
        created = AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="raw-receipt SECRET-RECEIPT",
            root_event_id="raw-root SECRET-ROOT",
            correlation_id="raw-correlation SECRET-CORRELATION",
            causation_id="raw-causation SECRET-CAUSATION",
        )
        self.acceptances[key] = created
        return created


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "TELEGRAM_AQUB_WEBHOOK_SECRET": "webhook SECRET-WEBHOOK",
        "GATE_AQUB_RECEIPT_SIGNING_KEY": (
            "0123456789abcdef0123456789abcdef SECRET-SIGNING"
        ),
    }
    values.update(changes)
    return Settings(**values)


def _binding() -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=TEST_CHAT_ID,
        ace_user_id="__unbound_ace__",
        alpha_user_id=TEST_ALPHA_USER_ID,
        ready_at=TEST_READY_AT,
        receipt_signing_key=TEST_SIGNING_KEY,
    )


def _update(**message_changes: object) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 501,
        "date": int((TEST_READY_AT + timedelta(seconds=1)).timestamp()),
        "chat": {"id": int(TEST_CHAT_ID), "type": "supergroup"},
        "from": {"id": int(TEST_ALPHA_USER_ID), "is_bot": False},
        "text": "/aqub_ping",
    }
    message.update(message_changes)
    return {"update_id": 500, "message": message}


def _client(settings: Settings, store: DurableStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_aqub_transport_store] = lambda: store
    app.dependency_overrides[get_aqub_production_binding] = _binding
    return TestClient(app)


def _post(
    client: TestClient,
    update: dict[str, object],
    secret: str = "webhook SECRET-WEBHOOK",
):
    return client.post(
        "/telegram/aqub/preflight",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json=update,
    )


def _receipt_fields(text: str) -> dict[str, str]:
    receipt_line = next(
        line for line in text.splitlines() if line.startswith("AQUB_RECEIPT ")
    )
    return {
        key: value
        for item in receipt_line.removeprefix("AQUB_RECEIPT ").split()
        for key, value in [item.split("=", 1)]
    }


def test_permanent_ping_route_owns_once_and_duplicate_reuses_receipt() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        first = _post(client, _update())
        second = _post(client, _update())

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["method"] == "sendMessage"
    assert second_body["method"] == "sendMessage"
    assert first_body["chat_id"] == int(TEST_CHAT_ID)

    first_receipt = _receipt_fields(first_body["text"])
    second_receipt = _receipt_fields(second_body["text"])
    assert first_receipt["status"] == "committed"
    assert first_receipt["role"] == "alpha"
    assert first_receipt["duplicate"] == "false"
    assert second_receipt["duplicate"] == "true"
    assert second_receipt["receipt_ref"] == first_receipt["receipt_ref"]
    assert second_receipt["root_ref"] == first_receipt["root_ref"]
    assert [command.external_id for command in store.commands] == ["500", "500"]


def test_aqub_route_does_not_enter_existing_dispatch_chain() -> None:
    assert all("aqub" not in route for route in TELEGRAM_WEBHOOK_CHAIN)


def test_unbound_group_touches_no_storage() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _update(chat={"id": -1007999, "type": "supergroup"}),
        )

    assert response.status_code == 200
    assert response.json()["owned"] is False
    assert store.commands == []


def test_wrong_webhook_secret_is_forbidden() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(), secret="wrong")

    assert response.status_code == 403
    assert store.commands == []


def test_ordinary_owned_message_without_runtime_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(text="обычное сообщение"))

    assert response.status_code == 503
    assert response.json()["detail"] == "AQUB runtime URL is unavailable"


def test_visible_receipt_reply_redacts_raw_identifiers_and_secrets() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update())

    reply_text = response.json()["text"]
    assert "AQUB_RECEIPT " in reply_text
    for forbidden in (
        "SECRET-RECEIPT",
        "SECRET-WEBHOOK",
        "SECRET-SIGNING",
        TEST_CHAT_ID,
        TEST_ALPHA_USER_ID,
        "/aqub_ping",
    ):
        assert forbidden not in reply_text
