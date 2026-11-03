"""Read-only runtime facts for an authenticated, separately authorized Ops observer.

Loaded process flags and actual Alembic heads are evidence; source TOML and
historical pre-deploy execution are deliberately not inferred from these facts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.readiness import CheckReadinessService, ReadinessProbeError
from app.core.config import Settings
from app.infrastructure.watch_voice_inventory import _object, iso
from app.infrastructure.watch_voice_journal import cycle_evidence, latest_cycle

_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_B = {"type": "boolean"}
_SHA = {"type": ["string", "null"], "pattern": "^[a-f0-9]{40}$"}
_UUID = {
    "type": ["string", "null"],
    "pattern": "^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
}
_HEADS = {
    "type": ["array", "null"],
    "maxItems": 4,
    "items": {"type": "string", "pattern": "^[0-9]{4}_[a-z][a-z0-9_]{0,120}$"},
}
RUNTIME_PREFLIGHT_SCHEMA = _object(
    {
        "schema": {"const": "gate.watch-voice.runtime-preflight.v1"},
        "observed_at": _S,
        "result": {"enum": ["OBSERVED", "UNAVAILABLE"]},
        "reason": {
            "enum": [
                None,
                "readiness_unavailable",
                "readiness_timeout",
                "readiness_failed",
                "database_unavailable",
                "migration_state_unavailable",
                "migration_heads_mismatch",
                "migration_state_invalid",
                "voice_journal_unavailable",
                "observation_timeout",
                "workspace_scope_mismatch",
            ]
        },
        "runtime": _object(
            {
                "source_sha": _SHA,
                **{
                    k: _UUID
                    for k in ("deployment_id", "project_id", "environment_id", "service_id")
                },
            }
        ),
        "configuration": _object(
            {
                "driver": {"enum": ["actions", "persistent", "disabled", "conflict"]},
                **{
                    k: _B
                    for k in (
                        "persistent_enabled",
                        "actions_enabled",
                        "scope_approved",
                        "calls_monitor_enabled",
                    )
                },
                "poll_seconds": {"type": "number", "minimum": 1, "maximum": 300},
            }
        ),
        "migrations": _object(
            {
                "expected_heads": _HEADS,
                "current_heads": _HEADS,
                "match": {"type": ["boolean", "null"]},
            }
        ),
        "worker": _object(
            {
                "running_driver_proven": _B,
                "observed_driver": {"enum": ["actions", "persistent", None]},
                "source_sha": _SHA,
                "last_cycle_id": _UUID,
                "phase": {"type": ["string", "null"], "pattern": "^[a-z][a-z0-9_]{0,31}$"},
                "deadline_at": _NS,
            }
        ),
        "effective_start_command": {"type": "null"},
        "effective_predeploy_command": {"type": "null"},
        "predeploy_execution_proven": {"const": False},
        "private_data_exposed": {"const": False},
        "mutations": {"const": 0},
    }
)


def safe_uuid(value: str | None) -> str | None:
    try:
        return str(UUID(value)) if value else None
    except (ValueError, TypeError):
        return None


def safe_sha(value: str | None) -> str | None:
    return value if value and re.fullmatch(r"[a-f0-9]{40}", value) else None


def safe_heads(values: frozenset[str]) -> list[str] | None:
    if len(values) > 4 or any(
        not re.fullmatch(r"[0-9]{4}_[a-z][a-z0-9_]{0,120}", v) for v in values
    ):
        return None
    return sorted(values)


def empty_runtime_preflight(settings: Settings, environ: Mapping[str, str]) -> dict[str, Any]:
    persistent, actions = (
        settings.gate_watch_voice_enabled,
        settings.gate_watch_voice_actions_enabled,
    )
    return {
        "schema": "gate.watch-voice.runtime-preflight.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "result": "UNAVAILABLE",
        "reason": "readiness_unavailable",
        "runtime": {
            "source_sha": safe_sha(settings.deployment_sha),
            **{
                key: safe_uuid(environ.get(name))
                for key, name in (
                    ("deployment_id", "RAILWAY_DEPLOYMENT_ID"),
                    ("project_id", "RAILWAY_PROJECT_ID"),
                    ("environment_id", "RAILWAY_ENVIRONMENT_ID"),
                    ("service_id", "RAILWAY_SERVICE_ID"),
                )
            },
        },
        "configuration": {
            "driver": "conflict"
            if persistent and actions
            else "persistent"
            if persistent
            else "actions"
            if actions
            else "disabled",
            "persistent_enabled": persistent,
            "actions_enabled": actions,
            "scope_approved": bool(
                settings.gate_watch_voice_data_gate_approved
                and settings.gate_workspace_id == "mark-personal"
                and settings.gate_watch_voice_workspace_key == "mark-personal"
            ),
            "poll_seconds": settings.gate_watch_voice_poll_seconds,
            "calls_monitor_enabled": settings.gate_dropbox_call_intake_enabled,
        },
        "migrations": {"expected_heads": None, "current_heads": None, "match": None},
        "worker": {
            "running_driver_proven": False,
            "observed_driver": None,
            "source_sha": None,
            "last_cycle_id": None,
            "phase": None,
            "deadline_at": None,
        },
        "effective_start_command": None,
        "effective_predeploy_command": None,
        "predeploy_execution_proven": False,
        "private_data_exposed": False,
        "mutations": 0,
    }


async def runtime_preflight(
    session: AsyncSession,
    settings: Settings,
    readiness: CheckReadinessService | None,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    result = empty_runtime_preflight(settings, environ)
    if (
        settings.gate_workspace_id != "mark-personal"
        or settings.gate_watch_voice_workspace_key != "mark-personal"
    ):
        result["reason"] = "workspace_scope_mismatch"
        return result
    if readiness is None:
        return result
    try:
        heads = await readiness.observe_heads()
    except TimeoutError:
        result["reason"] = "readiness_timeout"
        return result
    except ReadinessProbeError as exc:
        code = exc.code.value
        result["reason"] = (
            code
            if code in {"database_unavailable", "migration_state_unavailable"}
            else "readiness_failed"
        )
        return result
    except Exception:
        result["reason"] = "readiness_failed"
        return result
    expected, actual = safe_heads(heads.expected_heads), safe_heads(heads.current_heads)
    result["migrations"] = {
        "expected_heads": expected,
        "current_heads": actual,
        "match": (actual == expected) if expected and actual is not None else None,
    }
    if not expected or actual is None:
        result["reason"] = "migration_state_invalid"
        return result
    if actual != expected:
        result["reason"] = "migration_heads_mismatch"
        return result
    try:
        cycle = await latest_cycle(session, settings)
    except Exception:
        result["reason"] = "voice_journal_unavailable"
        return result
    phase = cycle.phase if cycle else None
    result["worker"] = {
        "running_driver_proven": cycle_evidence(cycle, settings, now=datetime.now(UTC)).fresh,
        "observed_driver": cycle.driver if cycle else None,
        "source_sha": safe_sha(cycle.source_sha if cycle else None),
        "last_cycle_id": safe_uuid(cycle.id if cycle else None),
        "phase": phase if phase and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", phase) else None,
        "deadline_at": iso(cycle.deadline_at) if cycle else None,
    }
    result["result"], result["reason"] = "OBSERVED", None
    return result
