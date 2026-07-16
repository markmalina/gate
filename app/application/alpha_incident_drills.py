"""Deterministic incident drills for Alpha's delivery-free release path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class AlphaIncident(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    DUPLICATE_FLOOD = "duplicate_flood"
    STALE_WORKER_CLAIM = "stale_worker_claim"
    PROVIDER_AMBIGUOUS = "provider_ambiguous"
    TELEGRAM_AMBIGUOUS = "telegram_ambiguous"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AlphaIncidentDisposition(StrEnum):
    REJECT_ACCEPTANCE = "reject_acceptance"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    BOUNDED_RECLAIM = "bounded_reclaim"
    TERMINAL_UNKNOWN = "terminal_unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class AlphaIncidentDrill:
    incident: AlphaIncident
    disposition: AlphaIncidentDisposition
    automatic_retry_allowed: bool
    provider_call_allowed: bool
    telegram_send_allowed: bool
    delivery_grant_allowed: bool
    operator_action: str


@dataclass(frozen=True, slots=True)
class AlphaIncidentDrillReport:
    schema_version: str
    passed: bool
    total_drills: int
    bounded_recovery_drills: int
    terminal_unknown_drills: int
    delivery_blocked_drills: int
    rollback_target: str
    drills: tuple[AlphaIncidentDrill, ...]


ALPHA_INCIDENT_DRILLS = (
    AlphaIncidentDrill(
        incident=AlphaIncident.DATABASE_UNAVAILABLE,
        disposition=AlphaIncidentDisposition.REJECT_ACCEPTANCE,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="restore_database_then_allow_upstream_redelivery",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.DUPLICATE_FLOOD,
        disposition=AlphaIncidentDisposition.IDEMPOTENT_REPLAY,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="observe_deduplication_and_queue_pressure",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.STALE_WORKER_CLAIM,
        disposition=AlphaIncidentDisposition.BOUNDED_RECLAIM,
        automatic_retry_allowed=True,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="reclaim_once_inside_worker_attempt_limit",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.PROVIDER_AMBIGUOUS,
        disposition=AlphaIncidentDisposition.TERMINAL_UNKNOWN,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="inspect_bounded_error_and_never_retry_automatically",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.TELEGRAM_AMBIGUOUS,
        disposition=AlphaIncidentDisposition.TERMINAL_UNKNOWN,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="record_unknown_and_never_retry_automatically",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.RATE_LIMITED,
        disposition=AlphaIncidentDisposition.BLOCKED,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="wait_for_a_new_eligible_invocation_window",
    ),
    AlphaIncidentDrill(
        incident=AlphaIncident.BUDGET_EXHAUSTED,
        disposition=AlphaIncidentDisposition.BLOCKED,
        automatic_retry_allowed=False,
        provider_call_allowed=False,
        telegram_send_allowed=False,
        delivery_grant_allowed=False,
        operator_action="block_until_a_new_budget_authorization_or_month",
    ),
)


def evaluate_alpha_incident_drills() -> AlphaIncidentDrillReport:
    """Fail closed if an incident gains delivery or an ambiguous retry."""

    drills = ALPHA_INCIDENT_DRILLS
    if len(drills) != len(AlphaIncident):
        raise ValueError("alpha_incident_drill_coverage_invalid")
    if {drill.incident for drill in drills} != set(AlphaIncident):
        raise ValueError("alpha_incident_drill_duplicate_or_missing")
    if any(
        drill.provider_call_allowed
        or drill.telegram_send_allowed
        or drill.delivery_grant_allowed
        for drill in drills
    ):
        raise ValueError("alpha_incident_drill_delivery_capability_invalid")

    terminal = tuple(
        drill
        for drill in drills
        if drill.disposition is AlphaIncidentDisposition.TERMINAL_UNKNOWN
    )
    if len(terminal) != 2 or any(
        drill.automatic_retry_allowed for drill in terminal
    ):
        raise ValueError("alpha_incident_drill_unknown_retry_invalid")

    bounded = tuple(
        drill
        for drill in drills
        if drill.disposition is AlphaIncidentDisposition.BOUNDED_RECLAIM
    )
    if len(bounded) != 1 or not bounded[0].automatic_retry_allowed:
        raise ValueError("alpha_incident_drill_reclaim_invalid")

    return AlphaIncidentDrillReport(
        schema_version="alpha-incident-drills-v1",
        passed=True,
        total_drills=len(drills),
        bounded_recovery_drills=len(bounded),
        terminal_unknown_drills=len(terminal),
        delivery_blocked_drills=sum(
            not drill.telegram_send_allowed for drill in drills
        ),
        rollback_target="fixture_canary",
        drills=drills,
    )


def alpha_incident_drill_payload() -> dict[str, object]:
    """Return content-free JSON evidence for CI and operator review."""

    report = evaluate_alpha_incident_drills()
    return {
        "schema_version": report.schema_version,
        "passed": report.passed,
        "total_drills": report.total_drills,
        "bounded_recovery_drills": report.bounded_recovery_drills,
        "terminal_unknown_drills": report.terminal_unknown_drills,
        "delivery_blocked_drills": report.delivery_blocked_drills,
        "rollback_target": report.rollback_target,
        "drills": [
            {
                **asdict(drill),
                "incident": drill.incident.value,
                "disposition": drill.disposition.value,
            }
            for drill in report.drills
        ],
    }
