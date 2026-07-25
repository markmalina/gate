"""Contract tests for the Universal Mayak durable SQL store."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.application.universal_mayak_demo import (
    DemoDeliveryReceipt,
    DemoDeliveryState,
    DemoDisposition,
    DemoInstallationState,
    DemoReservationState,
    DemoTerminalReceipt,
    UniversalMayakDemoError,
)
from app.infrastructure.universal_mayak_durable_store import (
    _event_uuid,
    _existing_reservation,
    _idempotency_uuid,
    _installation_state,
    _stored_input_digest,
)

TURN_ID = UUID("11111111-1111-1111-1111-111111111111")
GRANT_ID = UUID("22222222-2222-2222-2222-222222222222")
DIGEST = "a" * 64


def test_existing_nonterminal_turn_is_concurrency_block() -> None:
    turn = SimpleNamespace(
        id=TURN_ID,
        status="running",
        result_code=None,
    )

    reservation = _existing_reservation(turn, DIGEST, None, None)

    assert reservation.state is DemoReservationState.RUNNING
    assert reservation.turn_ref == str(TURN_ID)
    assert reservation.terminal is None


def test_sent_attempt_replays_exact_terminal_receipt() -> None:
    turn = SimpleNamespace(
        id=TURN_ID,
        status="proposed",
        result_code="mayak_demo_sent",
    )
    grant = SimpleNamespace(id=GRANT_ID)
    attempt = SimpleNamespace(
        state="sent",
        provider_message_id="opaque-provider-message",
        error_code=None,
    )
    grant.used_at = object()
    grant.revoked_at = None

    reservation = _existing_reservation(turn, DIGEST, grant, attempt)

    assert reservation.state is DemoReservationState.TERMINAL
    assert reservation.terminal == DemoTerminalReceipt(
        DemoDisposition.SENT,
        "mayak_demo_sent",
        DemoDeliveryReceipt(
            str(GRANT_ID),
            DemoDeliveryState.SENT,
            provider_message_ref="opaque-provider-message",
        ),
    )


def test_unknown_attempt_remains_terminal_and_never_retries() -> None:
    turn = SimpleNamespace(
        id=TURN_ID,
        status="failed",
        result_code="mayak_demo_delivery_exception",
    )
    grant = SimpleNamespace(id=GRANT_ID)
    attempt = SimpleNamespace(
        state="unknown",
        provider_message_id=None,
        error_code="mayak_demo_delivery_exception",
    )
    grant.used_at = object()
    grant.revoked_at = None

    reservation = _existing_reservation(turn, DIGEST, grant, attempt)

    assert reservation.state is DemoReservationState.TERMINAL
    assert reservation.terminal is not None
    assert reservation.terminal.disposition is DemoDisposition.UNKNOWN
    assert reservation.terminal.delivery is not None
    assert reservation.terminal.delivery.state is DemoDeliveryState.UNKNOWN


def test_fail_closed_installation_block_replays_as_blocked() -> None:
    turn = SimpleNamespace(
        id=TURN_ID,
        status="failed",
        result_code="mayak_demo_installation_not_active",
    )

    reservation = _existing_reservation(turn, DIGEST, None, None)

    assert reservation.state is DemoReservationState.TERMINAL
    assert reservation.terminal == DemoTerminalReceipt(
        DemoDisposition.BLOCKED,
        "mayak_demo_installation_not_active",
    )


def test_control_state_projects_from_existing_gate_tables() -> None:
    assert _installation_state("active", None) is DemoInstallationState.ACTIVE
    assert _installation_state("paused", None) is DemoInstallationState.PAUSED
    assert (
        _installation_state("active", "handoff_required")
        is DemoInstallationState.HUMAN_TAKEOVER
    )


def test_input_digest_marker_is_exact_and_fail_closed() -> None:
    assert _stored_input_digest([f"mayak-input-sha256:{DIGEST}"]) == DIGEST
    with pytest.raises(UniversalMayakDemoError):
        _stored_input_digest([])
    with pytest.raises(UniversalMayakDemoError):
        _stored_input_digest(["mayak-input-sha256:not-a-digest"])


def test_event_ref_is_exact_canonical_message_uuid() -> None:
    expected = UUID("33333333-3333-3333-3333-333333333333")
    assert _event_uuid(f"canonical_message_{expected}") == expected
    assert _idempotency_uuid(f"mayak_message_{expected}") == expected
    with pytest.raises(UniversalMayakDemoError):
        _event_uuid(str(expected))
    with pytest.raises(UniversalMayakDemoError):
        _idempotency_uuid(str(expected))


def test_store_reuses_existing_kernel_and_delivery_tables() -> None:
    source = __import__(
        "app.infrastructure.universal_mayak_durable_store",
        fromlist=["unused"],
    ).__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    for model in (
        "AgentSessionModel",
        "AgentTurnModel",
        "AgentInstallationModel",
        "DeliveryGrantModel",
        "AgentSendAttemptModel",
        "CanonicalMessage",
    ):
        assert model in text
    assert "on_conflict_do_nothing" in text
    assert "with_for_update" in text
    assert "TelegramClient" not in text
    assert "OpenAI" not in text
