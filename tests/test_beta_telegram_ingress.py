"""Isolation and fail-closed tests for the Beta Telegram ingress."""

import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from starlette.requests import Request
from app.api import beta_telegram_ingress as beta_ingress
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
        "GATE_BETA_CONNECTOR_ENABLED": True,
        "TELEGRAM_BETA_BOT_USERNAME": "BetaViaBot",
        "TELEGRAM_BETA_WEBHOOK_SECRET": "beta-secret",
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
            "text": "/beta@BetaViaBot fixture",
        },
    }


def _accept(settings: Settings, store: CapturingStore, update: dict[str, object]) -> object:
    return asyncio.run(
        beta_ingress.accept_beta_telegram_receipt(
            update=update,
            provided_secret="beta-secret",
            settings=settings,
            receipt_store=store,
            received_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )


def test_disabled_connector_touches_no_storage() -> None:
    store = CapturingStore()

    with pytest.raises(beta_ingress.HTTPException) as captured:
        _accept(_settings(GATE_BETA_CONNECTOR_ENABLED=False), store, _update())

    assert captured.value.status_code == 503
    assert store.commands == []


def test_beta_secret_is_independent_and_group_receipt_uses_beta_key() -> None:
    store = CapturingStore()

    acceptance = _accept(_settings(TELEGRAM_WEBHOOK_SECRET="business-secret"), store, _update())

    assert acceptance.duplicate is False
    assert len(store.commands) == 1
    assert store.commands[0].bot_key == "telegram_beta_group"


def test_private_chat_is_ignored_and_wrong_secret_fails_before_storage() -> None:
    store = CapturingStore()
    assert _accept(_settings(), store, _update("private")) is None

    with pytest.raises(beta_ingress.HTTPException) as secret_error:
        asyncio.run(
            beta_ingress.accept_beta_telegram_receipt(
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
    connector = _settings().telegram_connectors.beta_group

    assert connector.worker_enabled is True
    assert connector.effective_capabilities
    assert connector.send_authorizations == frozenset()


def test_mixed_or_unapproved_update_shape_is_ignored_before_storage() -> None:
    store = CapturingStore()
    mixed = _update()
    mixed["callback_query"] = {"id": "unexpected"}

    assert _accept(_settings(), store, mixed) is None
    assert store.commands == []


def test_private_update_gets_terminal_success_response_without_storage() -> None:
    store = CapturingStore()
    payload = json.dumps(_update("private")).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": beta_ingress.BETA_TELEGRAM_WEBHOOK_PATH,
            "headers": [],
        },
        receive,
    )
    result = asyncio.run(
        beta_ingress.beta_telegram_webhook(
            request=request,
            x_telegram_secret="beta-secret",
            settings=_settings(),
            receipt_store=store,
            beta_space_store=cast(beta_ingress.SqlBetaSpaceStore, object()),
        )
    )

    assert result == {
        "ok": True,
        "accepted": False,
        "ignored": True,
        "reason": "unsupported_update",
    }
    assert store.commands == []
