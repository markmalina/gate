from datetime import UTC, datetime, timedelta

from app.infrastructure.historical_debt_diagnostics import (
    _billing_verdict,
    _classify_reply_task_mismatch,
    _transport_verdict,
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
