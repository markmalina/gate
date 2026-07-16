"""Fail-closed incident evidence for Alpha."""

from app.application.alpha_incident_drills import (
    AlphaIncident,
    AlphaIncidentDisposition,
    alpha_incident_drill_payload,
    evaluate_alpha_incident_drills,
)


def test_all_incidents_block_delivery_and_keep_fixture_rollback() -> None:
    report = evaluate_alpha_incident_drills()

    assert report.passed is True
    assert report.total_drills == 7
    assert report.delivery_blocked_drills == report.total_drills
    assert report.rollback_target == "fixture_canary"
    assert all(not drill.provider_call_allowed for drill in report.drills)
    assert all(not drill.telegram_send_allowed for drill in report.drills)
    assert all(not drill.delivery_grant_allowed for drill in report.drills)


def test_ambiguous_outcomes_are_terminal_and_never_retried() -> None:
    report = evaluate_alpha_incident_drills()
    terminal = {
        drill.incident: drill
        for drill in report.drills
        if drill.disposition is AlphaIncidentDisposition.TERMINAL_UNKNOWN
    }

    assert set(terminal) == {
        AlphaIncident.PROVIDER_AMBIGUOUS,
        AlphaIncident.TELEGRAM_AMBIGUOUS,
    }
    assert all(not drill.automatic_retry_allowed for drill in terminal.values())


def test_only_stale_worker_claim_has_bounded_automatic_recovery() -> None:
    report = evaluate_alpha_incident_drills()
    retryable = [
        drill for drill in report.drills if drill.automatic_retry_allowed
    ]

    assert len(retryable) == 1
    assert retryable[0].incident is AlphaIncident.STALE_WORKER_CLAIM
    assert retryable[0].disposition is AlphaIncidentDisposition.BOUNDED_RECLAIM


def test_incident_payload_contains_no_message_or_provider_content() -> None:
    payload = alpha_incident_drill_payload()
    serialized = str(payload).casefold()

    assert payload["schema_version"] == "alpha-incident-drills-v1"
    assert payload["passed"] is True
    assert "message_text" not in serialized
    assert "prompt" not in serialized
    assert "api_key" not in serialized
    assert "group_id" not in serialized
