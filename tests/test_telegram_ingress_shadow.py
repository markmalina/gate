"""Fixture replay for the side-effect-free Telegram ingress shadow classifier."""

import logging

import pytest

from app.application.telegram_ingress_shadow import (
    BASE_WEBHOOK,
    SPRINT10_EDIT,
    SPRINT11_CALLBACK,
    SPRINT14_BUSINESS,
    SPRINT14_BUSINESS_ADMIN,
    SPRINT15_DIAGNOSTICS,
    SPRINT16_INBOX,
    SPRINT16_RAW,
    SPRINT17_SEND,
    SPRINT9_LEGACY_ADMIN,
    SPRINT18_DRAFT,
    classify_telegram_ingress,
    observe_telegram_ingress_shadow,
)


def _message(text: str, *, business: bool = False) -> dict:
    payload = {
        "message_id": 11,
        "chat": {"id": 22},
        "from": {"id": 33},
        "text": text,
    }
    if business:
        payload["business_connection_id"] = "bc-1"
    return {"update_id": 1, "message": payload}


@pytest.mark.parametrize(
    ("update", "expected_handler", "expected_command"),
    [
        (_message("hello"), BASE_WEBHOOK, None),
        (_message("/draft_deadbeef"), SPRINT18_DRAFT, "draft"),
        (_message("/send_deadbeef"), SPRINT17_SEND, "send"),
        (_message("/inbox"), SPRINT16_INBOX, "inbox"),
        (_message("/raw"), SPRINT16_RAW, "raw"),
        (_message("/events"), SPRINT15_DIAGNOSTICS, "events"),
        (_message("/status"), SPRINT14_BUSINESS_ADMIN, "status"),
        (_message("/business ru connection-1"), SPRINT14_BUSINESS_ADMIN, "business_assignment"),
        (_message("hello", business=True), SPRINT14_BUSINESS, None),
        (_message("/edit_deadbeef"), SPRINT10_EDIT, "edit"),
        (_message("/senddraft_deadbeef"), SPRINT9_LEGACY_ADMIN, "legacy_send"),
        (
            {
                "update_id": 2,
                "business_message": {
                    "message_id": 12,
                    "chat": {"id": 22},
                    "from": {"id": 33},
                    "text": "hello",
                    "business_connection_id": "bc-1",
                },
            },
            SPRINT14_BUSINESS,
            None,
        ),
        (
            {
                "update_id": 3,
                "business_connection": {
                    "id": "bc-1",
                    "user_chat_id": 22,
                    "user": {"id": 33},
                },
            },
            SPRINT14_BUSINESS,
            None,
        ),
        (
            {
                "update_id": 4,
                "callback_query": {
                    "id": "callback-1",
                    "data": "gate:send:deadbeef",
                    "from": {"id": 33},
                    "message": {"message_id": 12, "chat": {"id": 22}},
                },
            },
            SPRINT11_CALLBACK,
            "callback_send",
        ),
        ({"update_id": 5, "edited_business_message": {}}, SPRINT16_RAW, None),
        ({"update_id": 6, "callback_query": {"data": "not-gate"}}, SPRINT16_RAW, None),
    ],
)
def test_captured_update_fixture_selects_expected_handler(
    update: dict,
    expected_handler: str,
    expected_command: str | None,
) -> None:
    decision = classify_telegram_ingress(update)

    assert decision.handler == expected_handler
    assert decision.command == expected_command


def test_command_priority_matches_the_frozen_legacy_chain() -> None:
    decision = classify_telegram_ingress(_message("/draft_deadbeef", business=True))

    assert decision.handler == SPRINT18_DRAFT
    assert decision.reason == "draft_command"


def test_shadow_observation_logs_only_redacted_decision_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.telegram.ingress.shadow")
    secret_text = "private message body 42"

    with caplog.at_level(logging.INFO, logger=logger.name):
        decision = observe_telegram_ingress_shadow(_message(secret_text), logger=logger)

    assert decision.handler == BASE_WEBHOOK
    assert "Telegram ingress shadow decision" in caplog.text
    assert secret_text not in caplog.text
    assert "update_id" in caplog.text


def test_classifier_is_deterministic_and_side_effect_free() -> None:
    update = _message("/inbox")

    assert classify_telegram_ingress(update) == classify_telegram_ingress(update)
    assert update["message"]["text"] == "/inbox"
