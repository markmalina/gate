"""Sprint 11b inline action regression tests."""

import json
from typing import Any

import pytest
from starlette.requests import Request

from app.api import sprint11b_routes as sprint11b
from app.api.sprint11b_routes import _extract_draft_callback_action
from app.services.telegram import TelegramClient, _build_internal_reply_markup


class CapturingTelegramClient(TelegramClient):
    """Telegram client fake that records low-level payloads."""

    def __init__(self) -> None:
        super().__init__(credential="test-token", api_base_url="https://telegram.test")
        self.requests: list[dict[str, Any]] = []

    async def _post_telegram_json(
        self, *, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.requests.append({"url": url, "payload": payload})
        return {"ok": True, "result": {"message_id": 999}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_draft_created_message_gets_direct_edit_link_and_send_button() -> None:
    markup = _build_internal_reply_markup(
        "\n".join(
            [
                "Draft created: abcd1234",
                "task: task1234",
                "",
                "Привет.",
                "",
                "Not sent.",
            ]
        ),
        environ={
            "RAILWAY_PUBLIC_DOMAIN": "gate.example",
            "ADMIN_BEARER_TOKEN": "secret",
        },
    )

    assert markup == {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Редактировать",
                    "url": (
                        "https://gate.example/admin/drafts/abcd1234/web-edit"
                        "?token=5546bb49edb41003c548bc411ed8016a826c8c12"
                    ),
                },
                {"text": "✅ Отправить", "callback_data": "gate:send:abcd1234"},
            ]
        ]
    }


def test_draft_created_message_never_falls_back_to_edit_callback() -> None:
    markup = _build_internal_reply_markup(
        "Draft created: abcd1234",
        environ={},
    )

    assert markup == {
        "inline_keyboard": [
            [{"text": "✅ Отправить", "callback_data": "gate:send:abcd1234"}]
        ]
    }


def test_edit_link_message_gets_open_editor_button() -> None:
    markup = _build_internal_reply_markup(
        "\n".join(
            [
                "Edit draft: abcd1234",
                "https://gate.example/admin/drafts/abcd1234/edit?token=secret",
                "",
                "After save: /send_abcd1234",
            ]
        )
    )

    assert markup == {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Открыть редактор",
                    "url": "https://gate.example/admin/drafts/abcd1234/edit?token=secret",
                }
            ]
        ]
    }


@pytest.mark.anyio
async def test_explicit_empty_reply_markup_is_not_replaced_by_auto_buttons() -> None:
    telegram = CapturingTelegramClient()

    await telegram.send_message(
        chat_id="222",
        text="Draft created: abcd1234",
        reply_markup={"inline_keyboard": []},
    )

    assert telegram.requests == [
        {
            "url": "https://telegram.test/bottest-token/sendMessage",
            "payload": {
                "chat_id": "222",
                "text": "Draft created: abcd1234",
                "reply_markup": {"inline_keyboard": []},
            },
        }
    ]


@pytest.mark.anyio
async def test_edit_message_reply_markup_clears_keyboard_by_default() -> None:
    telegram = CapturingTelegramClient()

    await telegram.edit_message_reply_markup(chat_id="222", message_id=333)

    assert telegram.requests == [
        {
            "url": "https://telegram.test/bottest-token/editMessageReplyMarkup",
            "payload": {
                "chat_id": "222",
                "message_id": 333,
                "reply_markup": {"inline_keyboard": []},
            },
        }
    ]


def test_extracts_gate_draft_callback_action() -> None:
    action = _extract_draft_callback_action(
        {
            "update_id": 123,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 111},
                "data": "gate:send:abcd1234",
                "message": {
                    "message_id": 999,
                    "chat": {"id": 222, "type": "private"},
                },
            },
        }
    )

    assert action is not None
    assert action.callback_query_id == "callback-1"
    assert action.action == "send"
    assert action.draft_ref == "abcd1234"
    assert action.normalized.event_type == "callback_query"
    assert action.normalized.update_id == 123
    assert action.normalized.message_id == 999
    assert action.normalized.chat_id == "222"
    assert action.normalized.sender_id == "111"
    assert action.normalized.text == "gate:send:abcd1234"


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 123, "callback_query": {"id": "callback-1"}},
        {
            "update_id": 123,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 111},
                "data": "other:send:abcd1234",
                "message": {"message_id": 999, "chat": {"id": 222}},
            },
        },
        {
            "update_id": 123,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 111},
                "data": "gate:delete:abcd1234",
                "message": {"message_id": 999, "chat": {"id": 222}},
            },
        },
    ],
)
def test_rejects_non_gate_draft_callbacks(payload: dict[str, Any]) -> None:
    assert _extract_draft_callback_action(payload) is None


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["/test", "/inbox"])
async def test_normal_admin_commands_delegate_to_sprint10(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    payload = {
        "update_id": 500,
        "message": {
            "message_id": 30,
            "from": {"id": 111},
            "chat": {"id": 111},
            "text": text,
        },
    }
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/telegram/webhook",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 123),
        },
        receive,
    )
    delegated: dict[str, Any] = {}

    async def fake_sprint10_webhook(**kwargs: Any) -> dict[str, Any]:
        delegated.update(kwargs)
        return {"ok": True, "delegated": True}

    monkeypatch.setattr(
        sprint11b.sprint10,
        "sprint10_telegram_webhook",
        fake_sprint10_webhook,
    )

    result = await sprint11b.sprint11b_telegram_webhook(
        request=request,
        x_telegram_secret="test-secret",
        settings=object(),
        session=object(),
        telegram=object(),
    )

    assert result == {"ok": True, "delegated": True}
    assert delegated["request"] is request
