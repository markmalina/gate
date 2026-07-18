"""Voice status stays redacted and fails closed on billing ambiguity."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.infrastructure.telegram_voice_status import (
    TelegramVoiceOperationalStatus,
    classify_telegram_voice_status,
    telegram_voice_operational_status,
)
from app.models.billing import BillingEntitlementModel
from app.models.canonical import Workspace


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
        received_count=3,
        processing_count=0,
        delivered_count=1,
        retry_count=0,
        dead_letter_count=2,
        last_error_code="openai_transcription_rejected",
    )

    payload = status.payload()

    assert payload["received_count"] == 3
    assert payload["delivered_count"] == 1
    assert payload["dead_letter_count"] == 2
    assert payload["last_error_code"] == "openai_transcription_rejected"
    assert payload["telegram_delivery_allowed"] is False
    assert "provider_operation_ref" not in payload
    assert "reservation_key" not in payload
    assert "scope_ref" not in payload


class _Entitlements:
    def __init__(self, rows: list[BillingEntitlementModel]) -> None:
        self._rows = rows

    def all(self) -> list[BillingEntitlementModel]:
        return self._rows


class _Summary:
    def one(self) -> tuple[int, int, int, int, int]:
        return (1_100, 2_000, 1, 1, 0)


class _QueueSummary:
    def one(self) -> tuple[int, int, int, int, int]:
        return (3, 0, 1, 0, 2)


class _LatestError:
    def scalar_one_or_none(self) -> str:
        return "openai_transcription_rejected"


class _StatusSession:
    def __init__(self) -> None:
        workspace_id = uuid4()
        self.workspace = Workspace(
            id=workspace_id,
            key="mark-personal",
            display_name="Mark",
            is_active=True,
        )
        self.execute_calls = 0
        self.entitlement = BillingEntitlementModel(
            workspace_id=workspace_id,
            entitlement_key="gate-telegram-voice-v1",
            product="gate",
            scope_kind="telegram_connector",
            scope_ref="telegram_business",
            plan_key="gate-voice-initial",
            state="active",
            valid_from=datetime(2026, 7, 1, tzinfo=UTC),
            valid_until=None,
            hard_limit_microusd=1_000_000,
        )

    async def scalar(self, _statement: object) -> Workspace:
        return self.workspace

    async def scalars(self, _statement: object) -> _Entitlements:
        return _Entitlements([self.entitlement])

    async def execute(
        self, _statement: object
    ) -> _Summary | _QueueSummary | _LatestError:
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _Summary()
        if self.execute_calls == 2:
            return _QueueSummary()
        if self.execute_calls == 3:
            return _LatestError()
        raise AssertionError("unexpected voice status query")


def test_operational_query_returns_only_aggregated_budget_state() -> None:
    status = asyncio.run(
        telegram_voice_operational_status(
            _StatusSession(),  # type: ignore[arg-type]
            workspace_key="mark-personal",
            enabled=True,
            api_key_configured=True,
            model="gpt-4o-mini-transcribe",
            now=datetime(2026, 7, 17, tzinfo=UTC),
        )
    )

    assert status.state == "settlement_pending"
    assert status.period_key == "2026-07"
    assert status.settled_microusd == 1_100
    assert status.reserved_microusd == 2_000
    assert status.remaining_microusd == 996_900
    assert status.open_count == 1
    assert status.unknown_count == 0
    assert status.received_count == 3
    assert status.processing_count == 0
    assert status.delivered_count == 1
    assert status.retry_count == 0
    assert status.dead_letter_count == 2
    assert status.last_error_code == "openai_transcription_rejected"
