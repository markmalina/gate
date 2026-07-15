"""Pure projection tests for durable Telegram canonical facts."""

from datetime import UTC, datetime

import pytest
from app.application.canonical_messaging import ConnectorKind, WorkRoute
from app.application.telegram_canonical_projection import (
    TelegramCanonicalProjectionError,
    project_telegram_canonical_message,
)
from app.application.work_ownership import (
    BusinessScope,
    DirectionEvidence,
    OwnershipSignal,
    WorkOwnershipFacts,
    WorkOwnershipReason,
    evaluate_shadow_work_ownership,
)


def _private_update(text: str = "Око возрождения") -> dict[str, object]:
    return {
        "update_id": 51,
        "business_message": {
            "message_id": 71,
            "date": 1_784_086_400,
            "chat": {"id": 730000001, "type": "private"},
            "from": {"id": 740000001, "is_bot": False},
            "text": text,
        },
    }


def _oko_ownership(direction: DirectionEvidence) -> object:
    return evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.OKO_EXACT_ENROLLMENT,
            business_scope=BusinessScope.RU,
            direction_evidence=direction,
            routing_resolved=True,
        )
    )


def test_ru_oko_candidate_remains_human_routed_and_payload_free() -> None:
    ownership = _oko_ownership(DirectionEvidence.HUMAN_INBOUND)
    projection = project_telegram_canonical_message(
        _private_update(),
        workspace_id="mark-personal",
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        source_receipt_id="00000000-0000-0000-0000-000000000051",
        received_at=datetime(2026, 7, 15, tzinfo=UTC),
        direction_evidence=DirectionEvidence.HUMAN_INBOUND,
        reply_task_intent="create_inbound_human_text",
        ownership=ownership,
    )

    assert projection is not None
    assert projection.ownership.route is WorkRoute.HUMAN_REPLY_TASK
    assert projection.ownership.reason_code == (
        WorkOwnershipReason.OKO_EXACT_ENROLLMENT_CANDIDATE.value
    )
    assert projection.ownership.candidate_agent is not None
    assert "Око возрождения" not in repr(projection)
    assert "730000001" not in repr(projection)
    assert "740000001" not in repr(projection)


def test_owner_outbound_fact_does_not_create_human_reply_route() -> None:
    ownership = _oko_ownership(DirectionEvidence.OWNER_OUTBOUND)
    projection = project_telegram_canonical_message(
        _private_update(),
        workspace_id="mark-personal",
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        source_receipt_id="00000000-0000-0000-0000-000000000052",
        received_at=datetime(2026, 7, 15, tzinfo=UTC),
        direction_evidence=DirectionEvidence.OWNER_OUTBOUND,
        reply_task_intent="satisfy_existing",
        ownership=ownership,
    )

    assert projection is not None
    assert projection.ownership.route is WorkRoute.IGNORED
    assert projection.ownership.candidate_agent is None


def test_control_traffic_creates_no_canonical_message() -> None:
    ownership = _oko_ownership(DirectionEvidence.HUMAN_INBOUND)

    assert (
        project_telegram_canonical_message(
            _private_update("/status"),
            workspace_id="mark-personal",
            connector_key="telegram_business",
            connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
            source_receipt_id="00000000-0000-0000-0000-000000000053",
            received_at=datetime(2026, 7, 15, tzinfo=UTC),
            direction_evidence=DirectionEvidence.HUMAN_INBOUND,
            reply_task_intent="create_inbound_human_text",
            ownership=ownership,
            control_traffic=True,
        )
        is None
    )


def test_invalid_provider_dialog_id_fails_closed() -> None:
    update = _private_update()
    message = update["business_message"]
    assert isinstance(message, dict)
    message["chat"] = {"id": "untrusted-string", "type": "private"}

    with pytest.raises(
        TelegramCanonicalProjectionError,
        match="canonical_dialog_id_invalid",
    ):
        project_telegram_canonical_message(
            update,
            workspace_id="mark-personal",
            connector_key="telegram_business",
            connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
            source_receipt_id="00000000-0000-0000-0000-000000000054",
            received_at=datetime(2026, 7, 15, tzinfo=UTC),
            direction_evidence=DirectionEvidence.HUMAN_INBOUND,
            reply_task_intent="create_inbound_human_text",
            ownership=_oko_ownership(DirectionEvidence.HUMAN_INBOUND),
        )
