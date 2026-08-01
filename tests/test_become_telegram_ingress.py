"""Isolation and fail-closed tests for the BECOME Telegram ingress."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.api import become_telegram_ingress as become_ingress
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
        "GATE_BECOME_CONNECTOR_ENABLED": True,
        "TELEGRAM_BECOME_BOT_USERNAME": "BECOMEViaBot",
        "TELEGRAM_BECOME_WEBHOOK_SECRET": "become-secret",
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
            "text": "/become@BECOMEViaBot fixture",
        },
    }


def _accept(settings: Settings, store: CapturingStore, update: dict[str, object]) -> object:
    return asyncio.run(
        become_ingress.accept_become_telegram_receipt(
            update=update,
            provided_secret="become-secret",
            settings=settings,
            receipt_store=store,
            received_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )


def test_disabled_connector_touches_no_storage() -> None:
    store = CapturingStore()

    with pytest.raises(become_ingress.HTTPException) as captured:
        _accept(_settings(GATE_BECOME_CONNECTOR_ENABLED=False), store, _update())

    assert captured.value.status_code == 503
    assert store.commands == []


def test_become_secret_is_independent_and_group_receipt_uses_become_key() -> None:
    store = CapturingStore()

    acceptance = _accept(_settings(TELEGRAM_WEBHOOK_SECRET="business-secret"), store, _update())

    assert acceptance.duplicate is False
    assert len(store.commands) == 1
    assert store.commands[0].bot_key == "telegram_become_group"


def test_private_chat_and_wrong_secret_fail_before_storage() -> None:
    store = CapturingStore()
    with pytest.raises(become_ingress.HTTPException) as private_error:
        _accept(_settings(), store, _update("private"))
    assert private_error.value.status_code == 400

    with pytest.raises(become_ingress.HTTPException) as secret_error:
        asyncio.run(
            become_ingress.accept_become_telegram_receipt(
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
    connector = _settings().telegram_connectors.become_group

    assert connector.worker_enabled is True
    assert connector.effective_capabilities
    assert connector.send_authorizations == frozenset()


def test_mixed_or_unapproved_update_shape_fails_before_storage() -> None:
    store = CapturingStore()
    mixed = _update()
    mixed["callback_query"] = {"id": "unexpected"}

    with pytest.raises(become_ingress.HTTPException) as captured:
        _accept(_settings(), store, mixed)

    assert captured.value.status_code == 400
    assert store.commands == []
