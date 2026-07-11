"""Sprint 14b Telegram /status Business connection tests."""

from app.api import routes as legacy
from app.api.sprint14b_routes import (
    _format_status_with_business_connections,
    _is_status_command,
)


def test_status_formatter_includes_business_connection_details() -> None:
    status_payload = {
        "service": {"environment": "ci"},
        "deployment": {"commit_sha": "1234567890abcdef", "bot_username": "gate_m_bot"},
        "messages": {"count": 7},
        "reply_tasks": {"count": 3, "unanswered": 2},
    }
    text = _format_status_with_business_connections(
        status_payload=status_payload,
        business_connections=[
            {
                "account_alias": "telegram_mark_ru",
                "business_connection_id": "bc-ru-123",
                "configured": True,
                "is_enabled": True,
                "send_enabled": True,
                "telegram_user_id": 111,
                "user_chat_id": 222,
                "can_reply": True,
                "last_update_at": "2026-07-11T04:00:00+00:00",
            },
            {
                "account_alias": "telegram_mark_by",
                "business_connection_id": "bc-by-456",
                "configured": False,
                "is_enabled": False,
                "send_enabled": False,
                "telegram_user_id": None,
                "user_chat_id": None,
                "can_reply": None,
                "last_update_at": None,
            },
        ],
    )

    assert "Gate status" in text
    assert "Business connections:" in text
    assert "- telegram_mark_ru" in text
    assert "id: bc-ru-123" in text
    assert "enabled: yes" in text
    assert "configured: yes" in text
    assert "send: yes" in text
    assert "can_reply: yes" in text
    assert "- telegram_mark_by" in text
    assert "id: bc-by-456" in text
    assert "enabled: no" in text
    assert "configured: no" in text
    assert "send: no" in text
    assert "can_reply: unknown" in text


def test_status_formatter_shows_empty_business_connection_state() -> None:
    status_payload = {
        "service": {"environment": "ci"},
        "deployment": {"commit_sha": "unknown", "bot_username": "gate_m_bot"},
        "messages": {"count": 0},
        "reply_tasks": {"count": 0, "unanswered": 0},
    }

    text = _format_status_with_business_connections(
        status_payload=status_payload,
        business_connections=[],
    )

    assert "Gate status" in text
    assert "Business connections:" in text
    assert "- none stored yet" in text


def test_status_command_detection_matches_supported_aliases() -> None:
    assert _is_status_command(
        legacy.TelegramUpdateEvent(
            update_id=1,
            message_id=2,
            event_type="message",
            chat_id="1",
            sender_id="1",
            text="/status",
            business_connection_id=None,
        )
    )
    assert _is_status_command(
        legacy.TelegramUpdateEvent(
            update_id=1,
            message_id=2,
            event_type="message",
            chat_id="1",
            sender_id="1",
            text="статус",
            business_connection_id=None,
        )
    )
    assert not _is_status_command(
        legacy.TelegramUpdateEvent(
            update_id=1,
            message_id=2,
            event_type="message",
            chat_id="1",
            sender_id="1",
            text="/inbox",
            business_connection_id=None,
        )
    )
