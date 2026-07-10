"""Sprint 11b inline draft action tests."""

from app.api.sprint11b_routes import _extract_draft_callback_action
from app.services.telegram import _build_internal_reply_markup


def test_draft_created_message_gets_edit_and_send_buttons() -> None:
    markup = _build_internal_reply_markup(
        "Draft created: abcd1234\ntask: task1234\n\nDraft text\n\nNot sent."
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


def test_edit_link_message_gets_url_button() -> None:
    markup = _build_internal_reply_markup(
        "Edit draft: abcd1234\nhttps://gate.example/edit\n\nAfter save: /send_abcd1234"
    )

    assert markup == {
        "inline_keyboard": [
            [
                {
                    "text": "✏️ Открыть редактор",
                    "url": "https://gate.example/edit",
                }
            ]
        ]
    }


def test_callback_parser_normalizes_send_action() -> None:
    action = _extract_draft_callback_action(
        {
            "update_id": 123,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 111},
                "data": "gate:send:abcd1234",
                "message": {
                    "message_id": 20,
                    "chat": {"id": 222},
                },
            },
        }
    )

    assert action is not None
    assert action.action == "send"
    assert action.draft_ref == "abcd1234"
    assert action.normalized.event_type == "callback_query"
    assert action.normalized.chat_id == "222"
    assert action.normalized.sender_id == "111"


def test_callback_parser_rejects_unknown_or_short_action() -> None:
    base_update = {
        "update_id": 123,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 111},
            "message": {
                "message_id": 20,
                "chat": {"id": 222},
            },
        },
    }
    unknown = {
        **base_update,
        "callback_query": {
            **base_update["callback_query"],
            "data": "gate:delete:abcd1234",
        },
    }
    short = {
        **base_update,
        "callback_query": {
            **base_update["callback_query"],
            "data": "gate:send:abc",
        },
    }

    assert _extract_draft_callback_action(unknown) is None
    assert _extract_draft_callback_action(short) is None
