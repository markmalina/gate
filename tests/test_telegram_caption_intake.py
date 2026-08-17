"""Regression coverage for Telegram Business media-caption ingestion."""

from datetime import UTC, datetime

from app.api.routes import _extract_update_event
from app.application.canonical_messaging import ConnectorKind
from app.application.telegram_business_text_inbox import (
    is_telegram_business_text_cutover_update,
)
from app.application.telegram_canonical_projection import (
    project_telegram_canonical_message,
)
from app.application.telegram_ingress_identity import (
    build_telegram_business_identity_command,
)
from app.application.telegram_ingress_shadow import (
    build_telegram_ingress_routing_context,
    classify_telegram_ingress,
)
from app.application.telegram_message_content import extract_telegram_message_text
from app.application.work_ownership import (
    BusinessScope,
    DirectionEvidence,
    OwnershipSignal,
    WorkOwnershipFacts,
    evaluate_shadow_work_ownership,
)
from app.core.config import Settings


def _photo_update(caption: str = "synthetic workout report") -> dict[str, object]:
    return {
        "update_id": 9001,
        "business_message": {
            "message_id": 77,
            "date": 1_784_086_400,
            "business_connection_id": "ru-connection",
            "chat": {"id": 101, "type": "private"},
            "from": {"id": 202, "is_bot": False},
            "photo": [
                {
                    "file_id": "photo-small",
                    "file_unique_id": "photo-unique",
                    "width": 320,
                    "height": 240,
                    "file_size": 1234,
                }
            ],
            "caption": caption,
        },
    }


def _ownership() -> object:
    return evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.OKO_EXACT_ENROLLMENT,
            business_scope=BusinessScope.RU,
            direction_evidence=DirectionEvidence.HUMAN_INBOUND,
            routing_resolved=True,
        )
    )


def test_message_content_prefers_text_then_non_empty_caption() -> None:
    assert (
        extract_telegram_message_text({"text": "ordinary", "caption": "media"})
        == "ordinary"
    )
    assert extract_telegram_message_text({"caption": "media"}) == "media"
    assert extract_telegram_message_text({"text": "  ", "caption": "media"}) == "media"
    assert extract_telegram_message_text({"text": " ", "caption": "\n"}) is None


def test_photo_caption_is_admitted_without_becoming_an_admin_command() -> None:
    update = _photo_update("/status")

    identity = build_telegram_business_identity_command(update)
    assert identity is not None
    assert identity.text_present is True
    assert classify_telegram_ingress(update).command is None
    assert is_telegram_business_text_cutover_update(update)


def test_blank_photo_caption_remains_non_text() -> None:
    update = _photo_update("   ")

    identity = build_telegram_business_identity_command(update)
    assert identity is not None
    assert identity.text_present is False
    assert not is_telegram_business_text_cutover_update(update)


def test_photo_caption_reaches_canonical_legacy_and_shadow_projections() -> None:
    update = _photo_update()
    projection = project_telegram_canonical_message(
        update,
        workspace_id="mark-personal",
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        source_receipt_id="00000000-0000-0000-0000-000000009001",
        received_at=datetime(2026, 8, 17, tzinfo=UTC),
        direction_evidence=DirectionEvidence.HUMAN_INBOUND,
        reply_task_intent="create_inbound_human_text",
        ownership=_ownership(),
    )
    assert projection is not None
    assert projection.message.text_present is True
    assert projection.message.text_content == "synthetic workout report"

    event = _extract_update_event(update)
    assert event is not None
    assert event.text == "synthetic workout report"

    decision = classify_telegram_ingress(update)
    settings = Settings(_env_file=None).model_copy(
        update={"gate_account_ru_business_connection_id": "ru-connection"}
    )
    context = build_telegram_ingress_routing_context(
        update,
        decision=decision,
        settings=settings,
    )
    assert context.text_present is True
