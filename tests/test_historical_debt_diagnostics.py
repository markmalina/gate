import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from app.infrastructure.historical_debt_diagnostics import (
    _billing_verdict,
    _classify_reply_task_mismatch,
    _transport_verdict,
)
from app.infrastructure.mayak_parity_diagnostics import (
    exact_mayak_parity_join,
)

NOW = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)


def test_billing_replay_is_safe_only_for_one_bound_reservation() -> None:
    assert _billing_verdict(
        reason_code="billing_reservation_replayed",
        original_reservation_exists=True,
        reservation_count=1,
        usage_count=1,
        ledger_count=1,
    ) == "safe_replay_rejected"
    assert _billing_verdict(
        reason_code="billing_reservation_replayed",
        original_reservation_exists=True,
        reservation_count=2,
        usage_count=1,
        ledger_count=1,
    ) == "ambiguous"


def test_transport_unknown_without_result_remains_residual() -> None:
    assert _transport_verdict(
        reason_code="openai_transcription_transport_unknown",
        archive_present=True,
        transcript_complete=False,
    ) == "residual_provider_outcome_unknown"
    assert _transport_verdict(
        reason_code="openai_transcription_transport_unknown",
        archive_present=True,
        transcript_complete=True,
    ) == "provider_free_projection_recovery_available"


def test_reply_task_mismatch_classifies_current_durable_state() -> None:
    assert _classify_reply_task_mismatch(
        legacy_count=1,
        task_created_at=[],
        last_observed_at=NOW,
        scope_matched=True,
        person_identity_confirmed=True,
    ) == ("real_defect_missing_reply_task", True)
    assert _classify_reply_task_mismatch(
        legacy_count=1,
        task_created_at=[NOW + timedelta(seconds=1)],
        last_observed_at=NOW,
        scope_matched=True,
        person_identity_confirmed=True,
    ) == ("historical_residue_repaired", True)
    assert _classify_reply_task_mismatch(
        legacy_count=1,
        task_created_at=[NOW - timedelta(seconds=1)],
        last_observed_at=NOW,
        scope_matched=True,
        person_identity_confirmed=True,
    ) == ("real_defect_unexpected_reply_task", False)


def test_reply_task_mismatch_fails_closed_on_ambiguous_scope() -> None:
    assert _classify_reply_task_mismatch(
        legacy_count=1,
        task_created_at=[],
        last_observed_at=NOW,
        scope_matched=False,
        person_identity_confirmed=True,
    ) == ("ambiguous", None)
    assert _classify_reply_task_mismatch(
        legacy_count=1,
        task_created_at=[],
        last_observed_at=NOW,
        scope_matched=True,
        person_identity_confirmed=False,
    ) == ("ambiguous", None)


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExactJoinSession:
    def __init__(self, *, legacy_id: UUID, turn_count: int = 1) -> None:
        self.legacy_id = legacy_id
        self.turn_count = turn_count

    async def scalars(self, _statement: object) -> _Rows:
        return _Rows([self.legacy_id])

    async def execute(self, _statement: object) -> _Rows:
        turn_id = UUID("88888888-8888-8888-8888-888888888888")
        return _Rows(
            [
                (SimpleNamespace(id=turn_id), SimpleNamespace())
                for _ in range(self.turn_count)
            ]
        )


def test_exact_mayak_join_reclassifies_without_content_or_external_ids() -> None:
    legacy_id = UUID("77777777-7777-7777-7777-777777777777")
    result = asyncio.run(
        exact_mayak_parity_join(
            _ExactJoinSession(legacy_id=legacy_id),  # type: ignore[arg-type]
            workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
            canonical_message_id=UUID(
                "44444444-4444-4444-4444-444444444444"
            ),
            dialog_id=UUID("55555555-5555-5555-5555-555555555555"),
            account_id=UUID("22222222-2222-2222-2222-222222222222"),
            project_ids=frozenset(
                {UUID("33333333-3333-3333-3333-333333333333")}
            ),
            legacy_message_id=legacy_id,
            observed_reply_task_count=0,
        )
    )

    assert result == {
        "exact_join": True,
        "mayak_turn_ref": result["mayak_turn_ref"],
        "mayak_disposition": "processed",
        "effective_route": "agent_turn",
        "reply_task_expected": 0,
        "observed_reply_task_count": 0,
        "parity_policy_version": 3,
        "classification": "expected_zero_reply_task",
        "historical_false_positive": True,
    }
    assert set(result).isdisjoint(
        {
            "text",
            "username",
            "telegram_id",
            "payload",
            "provider_id",
            "billing_id",
        }
    )


def test_ambiguous_mayak_join_fails_closed_deterministically() -> None:
    legacy_id = UUID("77777777-7777-7777-7777-777777777777")
    session = _ExactJoinSession(legacy_id=legacy_id, turn_count=2)
    kwargs = {
        "workspace_id": UUID("11111111-1111-1111-1111-111111111111"),
        "canonical_message_id": UUID(
            "44444444-4444-4444-4444-444444444444"
        ),
        "dialog_id": UUID("55555555-5555-5555-5555-555555555555"),
        "account_id": UUID("22222222-2222-2222-2222-222222222222"),
        "project_ids": frozenset(
            {UUID("33333333-3333-3333-3333-333333333333")}
        ),
        "legacy_message_id": legacy_id,
        "observed_reply_task_count": 0,
    }

    first = asyncio.run(
        exact_mayak_parity_join(  # type: ignore[arg-type]
            session,
            **kwargs,
        )
    )
    second = asyncio.run(
        exact_mayak_parity_join(  # type: ignore[arg-type]
            session,
            **kwargs,
        )
    )

    assert first == second
    assert first["exact_join"] is False
    assert first["classification"] == "ambiguous"
    assert first["historical_false_positive"] is False
