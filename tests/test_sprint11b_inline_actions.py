# mypy: ignore-errors
"""Sprint 11b inline draft action regression tests."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import sprint10_routes as sprint10
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.main import app
from app.services.telegram import TelegramClient, _build_internal_reply_markup


class DummyTelegramClient(TelegramClient):
    """Telegram client fake used when the delegated route does not send."""

    def __init__(self) -> None:
        super().__init__(credential="")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def delegated_app_context(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    get_settings.cache_clear()
    delegated_updates: list[dict[str, Any]] = []

    async def override_session() -> AsyncIterator[object]:
        yield object()

    def override_telegram_client() -> TelegramClient:
        return DummyTelegramClient()

    async def fake_sprint9_webhook(**kwargs: Any) -> dict[str, Any]:
        delegated_updates.append(await kwargs["request"].json())
        return {
            "ok": True,
            "event_type": "message",
            "auto_reply": "delegated",
            "duplicate": False,
            "send_status": "sent",
        }

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = override_telegram_client
    monkeypatch.setattr(
        sprint10.sprint9,
        "sprint9_telegram_webhook",
        fake_sprint9_webhook,
    )
    yield delegated_updates
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _message_update(text: str, update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": 7819816320},
            "chat": {"id": 7819816320, "type": "private"},
            "date": 1,
            "text": text,
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(("text", "update_id"), [("/test", 501), ("/inbox", 502)])
async def test_existing_admin_commands_still_delegate_through_sprint10(
    delegated_app_context: list[dict[str, Any]],
    text: str,
    update_id: int,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json=_message_update(text, update_id),
        )

    assert response.status_code == 200
    assert response.json()["auto_reply"] == "delegated"
    assert delegated_app_context[-1]["message"]["text"] == text


def test_draft_created_message_builds_edit_and_send_buttons() -> None:
    markup = _build_internal_reply_markup(
        "Draft created: abcd1234\ntask: efgh5678\n\nЧерновик\n\nNot sent."
    )

    assert markup == {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Редактировать",
                    "callback_data": "gate:edit:abcd1234",
                },
                {
                    "text": "✅ Отправить",
                    "callback_data": "gate:send:abcd1234",
                },
            ]
        ]
    }


def test_edit_link_message_builds_url_button() -> None:
    markup = _build_internal_reply_markup(
        "Edit draft: abcd1234\nhttps://gate.example/admin/drafts/id/edit?token=tok"
    )

    assert markup == {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Открыть редактор",
                    "url": "https://gate.example/admin/drafts/id/edit?token=tok",
                }
            ]
        ]
    }


def test_callback_parser_normalizes_supported_draft_action() -> None:
    action = sprint10._extract_draft_callback_action(
        {
            "update_id": 503,
            "callback_query": {
                "id": "callback-503",
                "from": {"id": 7819816320},
                "data": "gate:send:abcd1234",
                "message": {
                    "message_id": 700,
                    "chat": {"id": 7819816320, "type": "private"},
                },
            },
        }
    )

    assert action is not None
    assert action.action == "send"
    assert action.draft_ref == "abcd1234"
    assert action.normalized.event_type == "callback_query"
    assert action.normalized.chat_id == "7819816320"


def test_callback_parser_rejects_unknown_or_short_actions() -> None:
    base = {
        "update_id": 504,
        "callback_query": {
            "id": "callback-504",
            "from": {"id": 7819816320},
            "message": {
                "message_id": 701,
                "chat": {"id": 7819816320, "type": "private"},
            },
        },
    }
    unknown = {
        **base,
        "callback_query": {
            **base["callback_query"],
            "data": "gate:delete:abcd1234",
        },
    }
    short = {
        **base,
        "callback_query": {
            **base["callback_query"],
            "data": "gate:send:abc",
        },
    }

    assert sprint10._extract_draft_callback_action(unknown) is None
    assert sprint10._extract_draft_callback_action(short) is None
