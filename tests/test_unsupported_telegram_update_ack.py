"""Regression tests for unsupported Telegram update acknowledgement."""

from collections.abc import AsyncIterator, Iterator

import pytest
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
)
from app.core.config import get_settings
from app.main import app
from app.services.telegram import TelegramClient
from httpx import ASGITransport, AsyncClient


class DummyTelegramClient(TelegramClient):
    """Unused Telegram client for unsupported-update tests."""

    def __init__(self) -> None:
        pass


class DummyReceiptStore:
    """Accept validated envelopes before unsupported legacy acknowledgement."""

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        return TelegramReceiptAcceptance(
            receipt_id=f"receipt-{command.update_id}",
            outbox_event_id=f"outbox-{command.update_id}",
            duplicate=False,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def unsupported_update_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    get_settings.cache_clear()

    async def override_session() -> AsyncIterator[object]:
        yield object()

    def override_telegram_client() -> TelegramClient:
        return DummyTelegramClient()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = override_telegram_client
    app.dependency_overrides[get_telegram_receipt_store] = lambda: DummyReceiptStore()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_unsupported_callback_query_is_acknowledged_with_200(
    unsupported_update_context: None,
) -> None:
    del unsupported_update_context
    payload = {
        "update_id": 123,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 111},
            "data": "gate:send:abcd1234",
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "ignored": True,
        "reason": "unsupported_update",
        "update_id": 123,
        "update_types": ["callback_query"],
    }


@pytest.mark.anyio
async def test_invalid_webhook_secret_still_returns_403(
    unsupported_update_context: None,
) -> None:
    del unsupported_update_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 124, "callback_query": {"id": "callback-2"}},
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_non_object_payload_still_returns_400(
    unsupported_update_context: None,
) -> None:
    del unsupported_update_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=[],
        )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_object_without_integer_update_id_still_returns_400(
    unsupported_update_context: None,
) -> None:
    del unsupported_update_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json={"callback_query": {"id": "callback-3"}},
        )

    assert response.status_code == 400
