"""Voice status stays redacted and fails closed on billing ambiguity."""

from app.infrastructure.telegram_voice_status import (
    TelegramVoiceOperationalStatus,
    classify_telegram_voice_status,
)


def classify(
    *,
    enabled: bool = True,
    api_key_configured: bool = True,
    entitlement_active: bool = True,
    remaining_microusd: int = 1_000_000,
    open_count: int = 0,
    unknown_count: int = 0,
) -> str:
    return classify_telegram_voice_status(
        enabled=enabled,
        api_key_configured=api_key_configured,
        entitlement_active=entitlement_active,
        remaining_microusd=remaining_microusd,
        open_count=open_count,
        unknown_count=unknown_count,
    )


def test_disabled_voice_is_explicit() -> None:
    assert classify(enabled=False) == "disabled"


def test_ready_requires_key_entitlement_and_budget() -> None:
    assert classify() == "ready"
    assert classify(api_key_configured=False) == "configuration_incomplete"
    assert classify(entitlement_active=False) == "configuration_incomplete"
    assert classify(remaining_microusd=0) == "budget_exhausted"


def test_open_and_unknown_reservations_are_visible_fail_closed() -> None:
    assert classify(open_count=1) == "settlement_pending"
    assert classify(enabled=False, unknown_count=1) == "reconciliation_required"


def test_payload_contains_no_operation_or_provider_references() -> None:
    status = TelegramVoiceOperationalStatus(
        state="disabled",
        enabled=False,
        api_key_configured=True,
        model="gpt-4o-mini-transcribe",
        entitlement_active=True,
        period_key="2026-07",
        hard_limit_microusd=1_000_000,
        settled_microusd=1_100,
        reserved_microusd=0,
        remaining_microusd=998_900,
        settled_count=1,
        open_count=0,
        unknown_count=0,
        reconciliation_required=False,
    )

    payload = status.payload()

    assert payload["telegram_delivery_allowed"] is False
    assert "provider_operation_ref" not in payload
    assert "reservation_key" not in payload
    assert "scope_ref" not in payload
