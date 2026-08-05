"""Redacted, fail-closed production evidence for Become Slice 3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

EXPECTED_ALEMBIC_HEAD = "0087_become_pilot_slice3_learner"
EXPECTED_RELATIONS_SOURCE_SHA = "337323ea854ca0ede76bb9eb8f31591c440e5168"
STATUS_SCHEMA = "gate.become-pilot-slice3-production-evidence@1"
RELATIONS_HEALTH_SCHEMA = "become.i4-service-health@1"


def _not_ready_relations(error_code: str) -> dict[str, Any]:
    return {
        "ready": False,
        "error_code": error_code,
        "source_sha": None,
        "execution_count": None,
        "provider_calls": None,
        "external_effects": None,
    }


def _health_url(endpoint: str) -> str | None:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/healthz", "", ""))


def validate_relations_health(payload: Any) -> dict[str, Any]:
    """Return only the bounded Slice 3 subset; reject every ambiguous receipt."""

    if not isinstance(payload, dict):
        return _not_ready_relations("slice3_relations_health_not_object")
    if (
        payload.get("schema") != RELATIONS_HEALTH_SCHEMA
        or payload.get("service") != "relations"
        or payload.get("status") != "ok"
    ):
        return _not_ready_relations("slice3_relations_health_schema_mismatch")
    if payload.get("source_sha") != EXPECTED_RELATIONS_SOURCE_SHA:
        return _not_ready_relations("slice3_relations_health_source_sha_mismatch")
    if payload.get("provider_calls") != 0 or payload.get("external_effects") != 0:
        return _not_ready_relations("slice3_relations_health_forbidden_effects")
    slice3 = payload.get("slice3_learner_link")
    if not isinstance(slice3, dict):
        return _not_ready_relations("slice3_relations_health_slice3_missing")
    execution_count = slice3.get("execution_count")
    if (
        not isinstance(execution_count, int)
        or isinstance(execution_count, bool)
        or execution_count < 0
        or slice3.get("provider_calls") != 0
        or slice3.get("external_effects") != 0
    ):
        return _not_ready_relations("slice3_relations_health_slice3_invalid")
    return {
        "ready": True,
        "error_code": None,
        "source_sha": EXPECTED_RELATIONS_SOURCE_SHA,
        "execution_count": execution_count,
        "provider_calls": 0,
        "external_effects": 0,
    }


async def _relations_health(
    http_client: httpx.AsyncClient,
    *,
    endpoint: str,
) -> dict[str, Any]:
    health_url = _health_url(endpoint)
    if health_url is None:
        return _not_ready_relations("slice3_relations_health_not_configured")
    try:
        response = await http_client.get(health_url, timeout=5.0)
        response.raise_for_status()
        return validate_relations_health(response.json())
    except (httpx.HTTPError, ValueError):
        return _not_ready_relations("slice3_relations_health_unavailable")


async def become_slice3_production_evidence(
    session: AsyncSession,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Observe only counts, booleans and immutable source digests."""

    observed_at = now or datetime.now(UTC)
    gates = {
        "runtime_enabled": settings.gate_become_pilot_slice3_enabled,
        "data_gate_approved": settings.gate_become_pilot_slice3_data_gate_approved,
        "delivery_enabled": settings.gate_become_pilot_slice3_delivery_enabled,
        "relations_enabled": settings.gate_become_pilot_slice3_relations_enabled,
    }
    caps = {
        "telegram_sends": settings.gate_become_pilot_slice3_max_telegram_sends,
        "relations_posts": settings.gate_become_pilot_slice3_max_relations_posts,
    }
    relations = await _relations_health(
        http_client,
        endpoint=settings.gate_become_pilot_slice3_relations_url,
    )
    try:
        if not hasattr(session, "begin_nested"):
            raise RuntimeError("slice3_nested_read_transaction_unavailable")
        async with session.begin_nested():
            alembic_head = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            row = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM become_slice3_learner_candidates) "
                        "AS candidate_total_count,"
                        "(SELECT count(*) FROM become_slice3_learner_candidates "
                        " WHERE state='candidate') AS candidate_count,"
                        "(SELECT count(*) FROM become_slice3_pilot_slots) AS slot_count,"
                        "(SELECT count(*) FROM become_slice3_invitations) AS invitation_count,"
                        "(SELECT count(*) FROM become_slice3_invitations "
                        " WHERE state='accepted') AS accepted_count,"
                        "(SELECT count(*) FROM become_slice3_invitations "
                        " WHERE state IN ('pending_delivery','delivered') "
                        " AND expires_at<=:now) AS expiry_due_count,"
                        "(SELECT count(*) FROM become_slice3_acceptances) AS acceptance_count,"
                        "(SELECT count(*) FROM become_slice3_deliveries) AS delivery_count,"
                        "(SELECT count(*) FROM become_slice3_deliveries "
                        " WHERE outcome='reserved') AS delivery_reserved_count,"
                        "(SELECT count(*) FROM become_slice3_deliveries "
                        " WHERE outcome='unknown') AS delivery_unknown_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox) AS outbox_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox "
                        " WHERE outcome='reserved') AS outbox_reserved_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox "
                        " WHERE outcome='unknown') AS relations_unknown_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox "
                        " WHERE outcome='executed') AS relations_executed_count,"
                        "(SELECT count(*) FROM become_slice3_learner_candidates "
                        " WHERE state='tombstoned') AS candidate_tombstone_count"
                    ),
                    {"now": observed_at},
                )
            ).mappings().one()
    except Exception:
        return {
            "schema": STATUS_SCHEMA,
            "ready": False,
            "error_code": "slice3_database_status_unavailable",
            "source_sha": settings.deployment_sha,
            "expected_alembic_head": EXPECTED_ALEMBIC_HEAD,
            "alembic_head": None,
            "gates": gates,
            "caps": caps,
            "slice3_status": None,
            "rollback_preflight": None,
            "counts": None,
            "relations": relations,
            "private_content_exposed": False,
            "provider_identifiers_exposed": False,
            "telegram_identifiers_exposed": False,
            "mutation_allowed": False,
        }

    counts = {key: int(value or 0) for key, value in row.items()}
    slice3_status = {
        "schema": "gate.become-pilot-slice3-status@1",
        "candidate_count": counts["candidate_count"],
        "slot_count": counts["slot_count"],
        "invitation_count": counts["invitation_count"],
        "accepted_count": counts["accepted_count"],
        "expiry_due_count": counts["expiry_due_count"],
        "acceptance_count": counts["acceptance_count"],
        "delivery_unknown_count": counts["delivery_unknown_count"],
        "relations_unknown_count": counts["relations_unknown_count"],
        "relations_executed_count": counts["relations_executed_count"],
        "candidate_tombstone_count": counts["candidate_tombstone_count"],
        "content_exposed": False,
        "provider_identifiers_exposed": False,
    }
    rollback_preflight = {
        "schema": "gate.become-pilot-slice3-rollback-preflight@1",
        "accepted_count": counts["acceptance_count"],
        "unresolved_delivery_count": (
            counts["delivery_reserved_count"] + counts["delivery_unknown_count"]
        ),
        "unresolved_relations_count": (
            counts["outbox_reserved_count"] + counts["relations_unknown_count"]
        ),
        "schema_downgrade_allowed": (
            counts["acceptance_count"] == 0
            and counts["delivery_reserved_count"] == 0
            and counts["delivery_unknown_count"] == 0
            and counts["outbox_reserved_count"] == 0
            and counts["relations_unknown_count"] == 0
        ),
        "application_rollback_allowed": (
            counts["delivery_reserved_count"] == 0
            and counts["delivery_unknown_count"] == 0
            and counts["outbox_reserved_count"] == 0
            and counts["relations_unknown_count"] == 0
        ),
        "content_exposed": False,
    }
    gates_default_off = not any(gates.values())
    caps_zero = not any(caps.values())
    local_counts_zero = not any(counts.values())
    source_sha_exact = (
        len(settings.deployment_sha) == 40
        and all(char in "0123456789abcdef" for char in settings.deployment_sha)
    )
    ready = bool(
        source_sha_exact
        and alembic_head == EXPECTED_ALEMBIC_HEAD
        and gates_default_off
        and caps_zero
        and local_counts_zero
        and relations["ready"]
        and relations["execution_count"] == 0
    )
    error_code: str | None = None
    if not source_sha_exact:
        error_code = "slice3_gate_source_sha_invalid"
    elif alembic_head != EXPECTED_ALEMBIC_HEAD:
        error_code = "slice3_alembic_head_mismatch"
    elif not gates_default_off:
        error_code = "slice3_gate_enabled"
    elif not caps_zero:
        error_code = "slice3_cap_nonzero"
    elif not local_counts_zero:
        error_code = "slice3_state_nonzero"
    elif not relations["ready"]:
        error_code = str(relations["error_code"])
    elif relations["execution_count"] != 0:
        error_code = "slice3_relations_execution_nonzero"

    return {
        "schema": STATUS_SCHEMA,
        "ready": ready,
        "error_code": error_code,
        "source_sha": settings.deployment_sha,
        "expected_alembic_head": EXPECTED_ALEMBIC_HEAD,
        "alembic_head": str(alembic_head) if alembic_head is not None else None,
        "gates": gates,
        "caps": caps,
        "slice3_status": slice3_status,
        "rollback_preflight": rollback_preflight,
        "counts": counts,
        "relations": relations,
        "private_content_exposed": False,
        "provider_identifiers_exposed": False,
        "telegram_identifiers_exposed": False,
        "mutation_allowed": False,
    }
