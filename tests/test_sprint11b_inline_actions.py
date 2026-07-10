"""Sprint 11b inline draft action tests."""

from app.api.sprint11b_routes import _extract_draft_callback_action
from app.services.telegram import _build_internal_reply_markup


def test_draft_created_message_gets_edit_and_send_buttons() -> None:
    markup = _build_internal_reply_markup(
        "\n".join(
            [
                "Draft created: abcd1234",
                "task: efgh5678",
                "",
                "Привет.",
                "",
                "Not sent.",
            ]
        )
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


def test_edit_link_message_gets_open_editor_button() -> None:
    url = "https://gate.example/admin/drafts/full-id/edit?token=secret"
    markup = _build_internal_reply_markup(
        "\n".join(
            [
                "Edit draft: abcd1234",
                url,
                "",
                "After save: /send_abcd1234",
            ]
        )
    )

    assert markup == {
        "inline_keyboard": [[{"text": "✏️ Открыть редактор", "url": url}]]
    }


def test_callback_action_parser_normalizes_send_action() -> None:
    action = _extract_draft_callback_action(
        {
            "update_id": 101,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 7819816320},
                "data": "gate:send:abcd1234",
                "message": {
                    "message_id": 42,
                    "chat": {"id": 7819816320},
                    "business_connection_id": "business-1",
                },
            },
        }
    )

    assert action is not None
    assert action.callback_query_id == "callback-1"
    assert action.action == "send"
    assert action.draft_ref == "abcd1234"
    assert action.normalized.event_type == "callback_query"
    assert action.normalized.message_id == 42
    assert action.normalized.chat_id == "7819816320"
    assert action.normalized.sender_id == "7819816320"
    assert action.normalized.business_connection_id == "business-1"


def test_callback_action_parser_rejects_unknown_or_short_action() -> None:
    base_callback = {
        "id": "callback-1",
        "from": {"id": 7819816320},
        "message": {"message_id": 42, "chat": {"id": 7819816320}},
    }

    unknown = {
        "update_id": 101,
        "callback_query": {**base_callback, "data": "gate:delete:abcd1234"},
    }
    short = {
        "update_id": 102,
        "callback_query": {**base_callback, "data": "gate:send:abc"},
    }

    assert _extract_draft_callback_action(unknown) is None
    assert _extract_draft_callback_action(short) is None


def test_non_callback_update_is_not_claimed_by_sprint11b_parser() -> None:
    action = _extract_draft_callback_action(
        {
            "update_id": 103,
            "message": {
                "message_id": 1,
                "chat": {"id": 7819816320},
                "from": {"id": 7819816320},
                "text": "/test",
            },
        }
    )

    assert action is None
