"""Pure guards for exclusive Business text cutover and inbox commands."""

from datetime import UTC, datetime

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    CanonicalMessageEnvelope,
    ConnectorKind,
    DialogIdentity,
    DialogKind,
    WorkOwnershipDecision,
    WorkRoute,
)
from app.application.telegram_business_text_inbox import (
    build_telegram_business_text_inbox_command,
    is_telegram_business_text_cutover_update,
)
from app.application.telegram_canonical_projection import TelegramCanonicalProjection
from app.application.work_ownership import WorkOwnershipState


def _update(text: str = "ordinary private text") -> dict[str, object]:
    return {
        "update_id": 42,
        "business_message": {
            "message_id": 77,
            "business_connection_id": "private-connection",
            "chat": {"id": 101, "type": "private"},
            "from": {
                "id": 202,
                "is_bot": False,
                "first_name": "Private",
                "last_name": "Person",
                "username": "private_user",
            },
            "text": text,
        },
    }


def _projection() -> TelegramCanonicalProjection:
    return TelegramCanonicalProjection(
        dialog=DialogIdentity(
            workspace_id="mark-personal",
            connector_key="telegram_business",
            connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
            provider_dialog_id="101",
            dialog_kind=DialogKind.PRIVATE,
        ),
        message=CanonicalMessageEnvelope(
            workspace_id="mark-personal",
            connector_key="telegram_business",
            source_receipt_id="11111111-1111-1111-1111-111111111111",
            provider_dialog_id="101",
            provider_message_id="77",
            provider_sender_id="202",
            direction=CanonicalMessageDirection.INBOUND,
            occurred_at=datetime(2026, 7, 18, tzinfo=UTC),
            text_present=True,
            text_content="ordinary private text",
        ),
        ownership=WorkOwnershipDecision(
            state=WorkOwnershipState.HUMAN_MANAGED,
            route=WorkRoute.HUMAN_REPLY_TASK,
            policy_version="test-policy",
        ),
    )


def test_only_ordinary_private_human_business_text_is_cut_over() -> None:
    assert is_telegram_business_text_cutover_update(_update())

    for text in ("", "   ", "ping", " PING ", "/status", "status", "/draft 123"):
        assert not is_telegram_business_text_cutover_update(_update(text))

    bot = _update()
    message = bot["business_message"]
    assert isinstance(message, dict)
    message["from"] = {"id": 202, "is_bot": True}
    assert not is_telegram_business_text_cutover_update(bot)

    non_text = _update()
    message = non_text["business_message"]
    assert isinstance(message, dict)
    message.pop("text")
    assert not is_telegram_business_text_cutover_update(non_text)


def test_inbox_command_uses_canonical_facts_and_hides_private_values() -> None:
    command = build_telegram_business_text_inbox_command(
        _update(),
        account_id="22222222-2222-2222-2222-222222222222",
        project_id="33333333-3333-3333-3333-333333333333",
        canonical_message_id="44444444-4444-4444-4444-444444444444",
        bot_username="gate_m_bot",
        update_id=42,
        business_connection_id="private-connection",
        projection=_projection(),
    )

    assert command.direction is CanonicalMessageDirection.INBOUND
    assert command.route is WorkRoute.HUMAN_REPLY_TASK
    assert command.counterparty_display_name == "Private Person"
    assert command.counterparty_username == "private_user"
    rendered = repr(command)
    for private in (
        "ordinary private text",
        "private-connection",
        "private_user",
        "Private Person",
        "22222222-2222-2222-2222-222222222222",
        "44444444-4444-4444-4444-444444444444",
    ):
        assert private not in rendered


def test_human_reply_route_rejects_outbound_direction() -> None:
    projection = _projection()
    outbound = TelegramCanonicalProjection(
        dialog=projection.dialog,
        message=CanonicalMessageEnvelope(
            workspace_id=projection.message.workspace_id,
            connector_key=projection.message.connector_key,
            source_receipt_id=projection.message.source_receipt_id,
            provider_dialog_id=projection.message.provider_dialog_id,
            provider_message_id=projection.message.provider_message_id,
            provider_sender_id=projection.message.provider_sender_id,
            direction=CanonicalMessageDirection.OUTBOUND,
            occurred_at=projection.message.occurred_at,
            text_present=True,
            text_content=projection.message.text_content,
        ),
        ownership=projection.ownership,
    )

    with pytest.raises(ValueError, match="human reply task requires inbound"):
        build_telegram_business_text_inbox_command(
            _update(),
            account_id="22222222-2222-2222-2222-222222222222",
            project_id="33333333-3333-3333-3333-333333333333",
            canonical_message_id="44444444-4444-4444-4444-444444444444",
            bot_username="gate_m_bot",
            update_id=42,
            business_connection_id="private-connection",
            projection=outbound,
        )


def test_inbox_command_allows_agent_route_without_changing_canonical_truth() -> None:
    command = build_telegram_business_text_inbox_command(
        _update(),
        account_id="22222222-2222-2222-2222-222222222222",
        project_id="33333333-3333-3333-3333-333333333333",
        canonical_message_id="44444444-4444-4444-4444-444444444444",
        bot_username="gate_m_bot",
        update_id=42,
        business_connection_id="private-connection",
        projection=_projection(),
        route_override=WorkRoute.AGENT_TURN,
    )

    assert command.direction is CanonicalMessageDirection.INBOUND
    assert command.route is WorkRoute.AGENT_TURN
    assert _projection().ownership.route is WorkRoute.HUMAN_REPLY_TASK
