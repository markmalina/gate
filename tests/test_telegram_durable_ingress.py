"""Focused guards for invariant durable Telegram ingress acceptance."""

import json
from collections.abc import Callable
from datetime import UTC
from typing import Any, cast

import pytest
from app.api import telegram_ingress as ingress
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import Settings
from app.services.telegram import TelegramClient
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.types import Message, Scope


class RecordingReceiptStore:
    """Record receipt commands without touching PostgreSQL."""

    def __init__(
        self,
        *,
        events: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.commands: list[AcceptTelegramReceiptCommand] = []
        self.events = events if events is not None else []
        self.error = error

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        self.events.append("accept")
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return TelegramReceiptAcceptance(
            receipt_id="private-receipt-id",
            outbox_event_id="private-outbox-id",
            duplicate=len(self.commands) > 1,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(
    *,
    secret: str = "test-secret",
    bot_username: str = "gate_m_bot",
) -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_WEBHOOK_SECRET": secret,
            "TELEGRAM_BOT_USERNAME": bot_username,
        }
    )


def _request(payload: object) -> Request:
    return _raw_request(json.dumps(payload).encode())


def _raw_request(body: bytes) -> Request:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = cast(
        Scope,
        {
            "type": "http",
            "method": "POST",
            "path": ingress.TELEGRAM_WEBHOOK_PATH,
            "headers": [],
        },
    )
    return Request(scope, receive)


async def _invoke(
    *,
    request: Request,
    settings: Settings,
    store: RecordingReceiptStore,
    x_telegram_secret: str | None = "test-secret",
) -> dict[str, Any]:
    return await ingress.durable_telegram_webhook(
        request=request,
        x_telegram_secret=x_telegram_secret,
        settings=settings,
        session=cast(AsyncSession, object()),
        telegram=cast(TelegramClient, object()),
        receipt_store=store,
    )


def test_durable_acceptance_has_no_runtime_mode_switch() -> None:
    settings = Settings.model_validate(
        {
            "TELEGRAM_WEBHOOK_SECRET": "private-webhook-secret",
            "GATE_TELEGRAM_DURABLE_ACCEPTANCE_MODE": "disabled",
        }
    )

    assert not hasattr(settings, "gate_telegram_durable_acceptance_mode")
    assert settings.telegram_webhook_secret.get_secret_value() == "private-webhook-secret"
    assert "private-webhook-secret" not in (
        f"{settings.telegram_webhook_secret!r} {settings.model_dump()}"
    )


@pytest.mark.anyio
async def test_durable_acceptance_commits_stable_envelope_before_legacy_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)
    payload = {
        "update_id": 4242,
        "business_message": {"text": "private-message-text"},
    }

    async def delegate(**kwargs: object) -> dict[str, Any]:
        delegated_request = cast(Request, kwargs["request"])
        assert await delegated_request.json() == payload
        events.append("delegate")
        return {"ok": True, "legacy": True}

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    result = await _invoke(
        request=_request(payload),
        settings=_settings(),
        store=store,
    )

    assert result == {"ok": True, "legacy": True}
    assert events == ["accept", "delegate"]
    assert len(store.commands) == 1
    command = store.commands[0]
    assert command.bot_key == "telegram_business"
    assert command.update_id == 4242
    assert command.payload == payload
    assert command.received_at.tzinfo is UTC
    assert "private-receipt-id" not in str(result)
    assert "private-outbox-id" not in str(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("settings_factory", "provided_secret", "request_factory", "status_code"),
    [
        (
            lambda: _settings(secret=""),
            "test-secret",
            lambda: _request({"update_id": 1}),
            503,
        ),
        (lambda: _settings(), "wrong-secret", lambda: _request({"update_id": 1}), 403),
        (lambda: _settings(), "не-ascii", lambda: _request({"update_id": 1}), 403),
        (lambda: _settings(), "test-secret", lambda: _request([]), 400),
        (
            lambda: _settings(),
            "test-secret",
            lambda: _request({"update_id": True}),
            400,
        ),
        (lambda: _settings(), "test-secret", lambda: _request({"update_id": -1}), 400),
        (
            lambda: _settings(),
            "test-secret",
            lambda: _request({"update_id": ingress.TELEGRAM_UPDATE_ID_MAX + 1}),
            400,
        ),
        (lambda: _settings(), "test-secret", lambda: _raw_request(b"not-json"), 400),
    ],
)
async def test_durable_acceptance_rejects_before_store_and_legacy(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[[], Settings],
    provided_secret: str,
    request_factory: Callable[[], Request],
    status_code: int,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)

    async def delegate(**_: object) -> dict[str, Any]:
        events.append("delegate")
        return {"ok": True}

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    with pytest.raises(HTTPException) as captured:
        await _invoke(
            request=request_factory(),
            settings=settings_factory(),
            store=store,
            x_telegram_secret=provided_secret,
        )

    assert captured.value.status_code == status_code
    assert events == []
    assert store.commands == []


@pytest.mark.anyio
async def test_acceptance_failure_is_retryable_redacted_and_not_delegated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(
        events=events,
        error=RuntimeError("private-message-text test-secret update-4242"),
    )

    async def delegate(**_: object) -> dict[str, Any]:
        events.append("delegate")
        return {"ok": True}

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    with caplog.at_level("ERROR", logger=ingress.logger.name):
        with pytest.raises(HTTPException) as captured:
            await _invoke(
                request=_request(
                    {
                        "update_id": 4242,
                        "business_message": {"text": "private-message-text"},
                    }
                ),
                settings=_settings(),
                store=store,
            )

    assert captured.value.status_code == 503
    assert events == ["accept"]
    assert "RuntimeError" in caplog.text
    assert "private-message-text" not in caplog.text
    assert "test-secret" not in caplog.text
    assert "4242" not in caplog.text


@pytest.mark.anyio
async def test_invalid_connector_identity_cannot_block_trusted_receipt_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)

    async def delegate(**_: object) -> dict[str, Any]:
        events.append("delegate")
        return {"ok": True, "legacy": True}

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    result = await _invoke(
        request=_request({"update_id": 4242, "callback_query": {"id": "x"}}),
        settings=_settings(bot_username=""),
        store=store,
    )

    assert result == {"ok": True, "legacy": True}
    assert events == ["accept", "delegate"]
    assert store.commands[0].bot_key == "telegram_business"


@pytest.mark.anyio
async def test_unexpected_acceptance_exception_is_retryable_not_legacy_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)

    async def fail_acceptance(**_: object) -> None:
        raise RuntimeError("private-message-text test-secret update-4242")

    async def delegate(**_: object) -> dict[str, Any]:
        events.append("delegate")
        return {"ok": True}

    monkeypatch.setattr(ingress, "_accept_durable_receipt", fail_acceptance)
    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    with caplog.at_level("ERROR", logger=ingress.logger.name):
        with pytest.raises(HTTPException) as captured:
            await _invoke(
                request=_request({"update_id": 4242}),
                settings=_settings(),
                store=store,
            )

    assert captured.value.status_code == 503
    assert events == []
    assert store.commands == []
    assert "RuntimeError" in caplog.text
    assert "private-message-text" not in caplog.text
    assert "test-secret" not in caplog.text
    assert "4242" not in caplog.text


@pytest.mark.anyio
async def test_first_and_duplicate_acceptances_both_reach_the_legacy_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)

    async def delegate(**_: object) -> dict[str, Any]:
        events.append("delegate")
        return {"ok": True, "legacy": True}

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", delegate)

    first = await _invoke(
        request=_request({"update_id": 4242, "callback_query": {"id": "first"}}),
        settings=_settings(),
        store=store,
    )
    duplicate = await _invoke(
        request=_request({"update_id": 4242, "callback_query": {"id": "first"}}),
        settings=_settings(),
        store=store,
    )

    assert first == duplicate == {"ok": True, "legacy": True}
    assert events == ["accept", "delegate", "accept", "delegate"]
    assert len(store.commands) == 2


@pytest.mark.anyio
async def test_downstream_failure_occurs_only_after_durable_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = RecordingReceiptStore(events=events)

    async def fail_downstream(**_: object) -> dict[str, Any]:
        events.append("delegate")
        raise RuntimeError("legacy failure")

    monkeypatch.setattr(ingress, "legacy_telegram_webhook", fail_downstream)

    with pytest.raises(RuntimeError, match="legacy failure"):
        await _invoke(
            request=_request({"update_id": 4242, "callback_query": {"id": "x"}}),
            settings=_settings(),
            store=store,
        )

    assert events == ["accept", "delegate"]
