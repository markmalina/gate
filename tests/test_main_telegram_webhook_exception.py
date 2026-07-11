"""Telegram webhook exception handling tests."""

import json

import pytest

from app.main import telegram_unhandled_exception_handler


class _FakeUrl:
    path = "/telegram/webhook"


class _FakeRequest:
    url = _FakeUrl()

    async def json(self) -> dict:
        return {
            "update_id": 123,
            "edited_business_message": {"business_connection_id": "bc-1"},
        }


@pytest.mark.anyio
async def test_telegram_webhook_unhandled_exception_is_acknowledged() -> None:
    response = await telegram_unhandled_exception_handler(
        _FakeRequest(),
        RuntimeError("boom"),
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload == {
        "ok": True,
        "ignored": True,
        "reason": "handler_exception",
        "error_type": "RuntimeError",
        "update_id": 123,
        "update_types": ["edited_business_message"],
    }
