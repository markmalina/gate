"""Isolation and fail-closed tests for the Beta Telegram ingress."""

import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from app.api import beta_telegram_ingress as beta_ingress
from app.application.beta_space_intake import (
    BetaSpaceBinding,
    BetaSpaceReservation,
)
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import Settings
from starlette.requests import Request


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


def _callback_update(chat_type: str = "supergroup") -> dict[str, object]:
    return {
        "update_id": 82,
        "callback_query": {
            "id": "callback-82",
            "from": {"id": 740000001, "is_bot": False},
            "data": "beta:katya:consent:yes",
            "message": {
                "message_id": 92,
                "chat": {"id": -100730000001, "type": chat_type},
            },
        },
    }


def test_group_callback_is_durably_accepted_for_subject_verification() -> None:
    store = CapturingStore()

    acceptance = _accept(_settings(), store, _callback_update())

    assert acceptance.duplicate is False
    assert len(store.commands) == 1
    assert store.commands[0].bot_key == "telegram_beta_group"


def test_private_callback_is_ignored_before_storage() -> None:
    store = CapturingStore()

    assert _accept(_settings(), store, _callback_update("private")) is None
    assert store.commands == []


def test_unbound_wedding_message_cannot_create_binding_or_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CapturingStore()
    update = {
        "update_id": 496003,
        "message": {
            "message_id": 1,
            "date": 1_786_040_000,
            "chat": {
                "id": -100496001,
                "type": "supergroup",
                "title": "Свадьба в Минске",
            },
            "from": {
                "id": 496002,
                "is_bot": False,
                "first_name": "Марк",
            },
            "text": "Бета, начинаем собирать свадьбу в Минске.",
        },
    }
    payload = json.dumps(update).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    class UnboundSpaceStore:
        def __init__(self) -> None:
            self.resolve_calls: list[str] = []

        async def resolve_binding(self, *, telegram_chat_id: str) -> None:
            self.resolve_calls.append(telegram_chat_id)
            return None

    space_store = UnboundSpaceStore()

    async def unexpected_drain(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unbound updates must not trigger provider dispatch")

    def unexpected_provider(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unbound updates must not acquire a provider client")

    monkeypatch.setattr(beta_ingress, "_drain_beta_checkpoint_dispatch", unexpected_drain)
    monkeypatch.setattr(beta_ingress, "get_beta_telegram_client", unexpected_provider)
    monkeypatch.setattr(beta_ingress, "get_beta_model_http_client", unexpected_provider)
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
            settings=_settings(
                GATE_ENV="production",
                GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
            ),
            receipt_store=store,
            beta_space_store=cast(beta_ingress.SqlBetaSpaceStore, space_store),
            session=cast(beta_ingress.AsyncSession, object()),
        )
    )

    assert result == {
        "ok": True,
        "accepted": True,
        "duplicate": False,
        "space_processing": "unbound_chat",
        "work_queued": False,
    }
    assert len(store.commands) == 1
    assert space_store.resolve_calls == ["-100496001"]


def test_bound_fact_webhook_only_admits_durable_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CapturingStore()
    update = {
        "update_id": 496004,
        "message": {
            "message_id": 2,
            "date": 1_786_040_001,
            "chat": {"id": -100496001, "type": "supergroup"},
            "from": {"id": 496002, "is_bot": False},
            "text": "Бета, сохрани этот факт.",
        },
    }
    payload = json.dumps(update).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    class BoundSpaceStore:
        async def resolve_binding(self, *, telegram_chat_id: str) -> BetaSpaceBinding:
            assert telegram_chat_id == "-100496001"
            return BetaSpaceBinding(
                binding_id="00000000-0000-0000-0000-000000000001",
                workspace_id="00000000-0000-0000-0000-000000000002",
                tenant_ref="beta-user-mark-wedding-001",
                space_ref="spc_fcdd8e57a7e832840afcc187fb1d4b2c",
                installation_ref="installation-test",
                telegram_chat_id=telegram_chat_id,
                subject_telegram_user_id="496002",
                consent_sha256="a" * 64,
                ready_at=datetime(2026, 8, 1, tzinfo=UTC),
                state="consented",
                actor_policy="group_members",
            )

        async def append_fact(self, **_kwargs: object) -> BetaSpaceReservation:
            return BetaSpaceReservation(
                True,
                "00000000-0000-0000-0000-000000000003",
            )

    def unexpected_effect(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("webhook must not call a provider, Telegram, or GitHub")

    monkeypatch.setattr(beta_ingress, "get_beta_telegram_client", unexpected_effect)
    monkeypatch.setattr(beta_ingress, "get_beta_model_http_client", unexpected_effect)
    monkeypatch.setattr(beta_ingress, "_drain_beta_checkpoint_dispatch", unexpected_effect)
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
            settings=_settings(
                GATE_ENV="production",
                GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
            ),
            receipt_store=store,
            beta_space_store=cast(
                beta_ingress.SqlBetaSpaceStore,
                BoundSpaceStore(),
            ),
            session=cast(beta_ingress.AsyncSession, object()),
        )
    )
    assert result == {
        "ok": True,
        "accepted": True,
        "duplicate": False,
        "space_processing": "fact_accepted",
        "work_queued": True,
    }
