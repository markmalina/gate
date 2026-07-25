"""Safety tests for the exact Telegram Business Mayak delivery adapter."""

from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from app.application.universal_mayak_demo import DemoDeliveryState
from app.infrastructure.universal_mayak_delivery import (
    _grant_id,
    _provider_failure,
    _provider_message_id,
    _terminal_receipt,
)

GRANT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_delivery_reference_is_an_exact_gate_uuid() -> None:
    assert _grant_id(str(GRANT_ID)) == GRANT_ID
    with pytest.raises(ValueError, match="mayak_business_delivery_ref_invalid"):
        _grant_id("provider-selected-target")


def test_provider_message_id_requires_confirmed_telegram_result() -> None:
    assert _provider_message_id({"ok": True, "result": {"message_id": 42}}) == "42"
    assert _provider_message_id({"ok": False, "result": {"message_id": 42}}) is None
    assert _provider_message_id({"ok": True, "result": {}}) is None
    assert _provider_message_id({"ok": True, "result": {"message_id": True}}) is None


def test_transport_failure_is_terminal_unknown() -> None:
    request = httpx.Request("POST", "https://api.telegram.org")
    state, code = _provider_failure(httpx.ConnectError("offline", request=request))

    assert state is DemoDeliveryState.UNKNOWN
    assert code == "telegram_transport_unknown"


def test_terminal_attempt_replays_without_new_provider_call() -> None:
    receipt = _terminal_receipt(
        str(GRANT_ID),
        SimpleNamespace(
            state="unknown",
            provider_message_id=None,
            error_code="telegram_transport_unknown",
        ),
    )

    assert receipt is not None
    assert receipt.state is DemoDeliveryState.UNKNOWN
    assert receipt.error_code == "telegram_transport_unknown"


def test_nonterminal_attempt_does_not_project_a_receipt() -> None:
    assert (
        _terminal_receipt(
            str(GRANT_ID),
            SimpleNamespace(
                state="sending",
                provider_message_id=None,
                error_code=None,
            ),
        )
        is None
    )
