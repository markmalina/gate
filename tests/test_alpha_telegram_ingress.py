"""Isolation and fail-closed tests for the Alpha Telegram ingress."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api.alpha_telegram_ingress import accept_alpha_telegram_receipt
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import Settings


class CapturingStore:
    def __init__(self) -> None:
        self.commands: list[AcceptTelegramReceiptCommand] = []

    async def create_if_absent(
        self, command: AcceptTelegramReceiptCommand
    ) -> TelegramReceiptAcceptance:
        self.commands.append(command)
        return TelegramReceiptAcceptance(
            receipt_id="receipt-1", outbox_event_id="outbox-1", duplicate=False
        )


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "GATE_ALPHA_CONNECTOR_ENABLED": True,
        "TELEGRAM_ALPHA_BOT_USERNAME": "alpha_test_bot",
        "TELEGRAM_ALPHA_WEBHOOK_SECRET": "alpha-secret",
    }
    values.update(changes)
    return Settings(**values)


def _update(chat_type: str = "supergroup") -> dict[str, object]:
    return {
        "update_id": 81,
        "message": {
            "message_id": 91,
            "chat": {"id": -100730000001, "type": chat_type},
            "from": {"id": 740000001, "is_bot": False},
            "text": "/alpha fixture",
        },
    }


def _accept(settings: Settings, store: CapturingStore, update: dict[str, object]) -> object:
    return asyncio.run(
        accept_alpha_telegram_receipt(
            update=update,
            provided_secret="alpha-secret",
            settings=settings,
            receipt_store=store,
            received_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )


def test_disabled_connector_touches_no_storage() -> None:
    store = CapturingStore()

    with pytest.raises(HTTPException) as captured:
        _accept(_settings(GATE_ALPHA_CONNECTOR_ENABLED=False), store, _update())

    assert captured.value.status_code == 503
    assert store.commands == []


def test_alpha_secret_is_independent_and_group_receipt_uses_alpha_key() -> None:
    store = CapturingStore()

    acceptance = _accept(_settings(TELEGRAM_WEBHOOK_SECRET="business-secret"), store, _update())

    assert acceptance.duplicate is False
    assert len(store.commands) == 1
    assert store.commands[0].bot_key == "telegram_alpha_group"


def test_private_chat_and_wrong_secret_fail_before_storage() -> None:
    store = CapturingStore()
    with pytest.raises(HTTPException) as private_error:
        _accept(_settings(), store, _update("private"))
    assert private_error.value.status_code == 400

    with pytest.raises(HTTPException) as secret_error:
        asyncio.run(
            accept_alpha_telegram_receipt(
                update=_update(),
                provided_secret="business-secret",
                settings=_settings(),
                receipt_store=store,
                received_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
        )
    assert secret_error.value.status_code == 403
    assert store.commands == []


def test_enabled_registry_still_has_no_send_authorization() -> None:
    connector = _settings().telegram_connectors.alpha_group

    assert connector.worker_enabled is True
    assert connector.effective_capabilities
    assert connector.send_authorizations == frozenset()
