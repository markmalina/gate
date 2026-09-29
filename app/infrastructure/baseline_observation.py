"""Schema-0111, physical-session evidence emitted by the actual web process.

No application tables or credentials are created. The last query of one pinned
connection carries only a strict, bounded metadata marker. An authenticated Ops
reader must independently verify provider inventory, session visibility and DB
identity; the marker alone is never a fleet, drain or deployment attestation.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from decimal import Decimal
from time import monotonic
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import Settings
from app.services.process_send_fence import (
    BaselineSendClosed,
    ProcessSendFence,
    install_process_send_fence,
    process_send_fence,
)

logger = logging.getLogger(__name__)
BASELINE_SCHEMA = "0111_reply_documents"
PREFIX = "SELECT 1 /*gate_g1a_v1;"
SUFFIX = ";end=gate_g1a_v1*/"
KEYS = (
    "boot",
    "source",
    "replica",
    "deployment",
    "workspace",
    "config",
    "identity",
    "schema",
    "send",
    "worker",
    "inflight",
)
_HEX = re.compile(r"[0-9a-f]+\Z")
_CONFIG_FIELDS = (
    "gate_env",
    "gate_default_mode",
    "gate_send_enabled",
    "gate_reply_sequence_worker_enabled",
    "gate_reply_sequence_poll_seconds",
    "gate_account_ru_send_enabled",
    "gate_account_by_send_enabled",
    "gate_document_send_enabled",
    "gate_telegram_worker_enabled",
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def process_metadata(
    settings: Settings, *, boot: str, environ: Mapping[str, str] = os.environ
) -> dict[str, str]:
    """Hash only an explicit non-secret config allowlist; never hash credentials."""
    if not re.fullmatch(r"[0-9a-f]{32}", boot):
        raise ValueError("invalid_boot")
    if not re.fullmatch(r"[0-9a-f]{40}", settings.deployment_sha):
        raise ValueError("source_unknown")
    replica, deployment = environ.get("RAILWAY_REPLICA_ID"), environ.get("RAILWAY_DEPLOYMENT_ID")
    if not replica or not deployment or not settings.gate_workspace_id:
        raise ValueError("provider_binding_unknown")
    return {
        "boot": boot,
        "source": settings.deployment_sha,
        "replica": digest(replica),
        "deployment": digest(deployment),
        "workspace": digest(settings.gate_workspace_id),
        "config": digest(config_evidence(settings)),
    }


def config_evidence(settings: Settings) -> dict[str, object]:
    values: dict[str, object] = {key: getattr(settings, key) for key in _CONFIG_FIELDS}
    # Decimal string avoids Python 5.0 versus JavaScript 5 JSON differences.
    values["gate_reply_sequence_poll_seconds"] = format(
        Decimal(str(settings.gate_reply_sequence_poll_seconds)).normalize(), "f"
    )
    return values


def marker_query(fields: Mapping[str, str]) -> str:
    """Exact grammar prevents arbitrary SQL/comment text and truncation ambiguity."""
    if set(fields) != set(KEYS):
        raise ValueError("invalid_marker_keys")
    for key in KEYS:
        value = fields[key]
        length = 32 if key == "boot" else 40 if key == "source" else 64
        if key in {"send", "worker"}:
            valid = value in {"0", "1"}
        elif key == "inflight":
            valid = bool(re.fullmatch(r"0|[1-9][0-9]{0,5}", value))
        elif key == "schema":
            valid = value == BASELINE_SCHEMA
        else:
            valid = len(value) == length and bool(_HEX.fullmatch(value))
        if not valid:
            raise ValueError("invalid_marker_value")
    query = PREFIX + ";".join(f"{key}={fields[key]}" for key in KEYS) + SUFFIX
    if len(query.encode("ascii")) >= 1024:
        raise ValueError("marker_too_long")
    return query


def parse_marker(query: str) -> dict[str, str]:
    if not query.startswith(PREFIX) or not query.endswith(SUFFIX):
        raise ValueError("invalid_marker")
    pairs = query[len(PREFIX) : -len(SUFFIX)].split(";")
    if len(pairs) != len(KEYS):
        raise ValueError("invalid_marker")
    fields = dict(pair.split("=", 1) for pair in pairs)
    if marker_query(fields) != query:
        raise ValueError("invalid_marker")
    return fields


async def read_identity(connection: AsyncConnection) -> tuple[str, int, object]:
    """Actual cluster/database/schema, not a DSN/role/private-address comparison."""
    row = (
        await connection.execute(
            text(
                "SELECT c.system_identifier::text, current_database(), d.oid::text, "
                "current_schema(), pg_backend_pid(), a.backend_start "
                "FROM pg_control_system() c JOIN pg_database d ON d.datname=current_database() "
                "JOIN pg_stat_activity a ON a.pid=pg_backend_pid()"
            )
        )
    ).one()
    versions = (
        (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    )
    if versions != [BASELINE_SCHEMA]:
        raise ValueError("unsupported_schema")
    if not all(row[i] is not None for i in range(6)):
        raise ValueError("identity_unknown")
    return digest(list(row[:4])), int(row[4]), row[5]


async def observe_connection(
    connection: AsyncConnection, *, fence: ProcessSendFence, metadata: dict[str, str]
) -> None:
    """Pin one physical backend. No transaction or application lock is retained."""
    settings = (
        await connection.execute(
            text(
                "SELECT current_setting('track_activities'), "
                "pg_size_bytes(current_setting('track_activity_query_size'))"
            )
        )
    ).one()
    if settings[0] != "on" or int(settings[1]) < 1024:
        raise ValueError("activity_tracking_unavailable")
    await connection.execute(
        text("SELECT set_config('application_name', :name, false)"),
        {"name": "gate-g1a:" + metadata["boot"]},
    )
    original_backend: tuple[int, object] | None = None
    while True:
        identity, pid, backend_start = await read_identity(connection)
        backend = (pid, backend_start)
        if original_backend is not None and backend != original_backend:
            raise ValueError("physical_backend_changed")
        original_backend = backend
        fields = {
            **metadata,
            "identity": identity,
            "schema": BASELINE_SCHEMA,
            "send": str(int(fence.settings.gate_send_enabled)),
            "worker": str(int(fence.settings.gate_reply_sequence_worker_enabled)),
            "inflight": str(fence.inflight),
        }
        await connection.execute(text(marker_query(fields)))
        fence.last_observed = monotonic()
        await asyncio.sleep(5)


async def run_baseline_observer(*, engine: AsyncEngine, fence: ProcessSendFence) -> None:
    """One process boot identity, one reserved pool connection, bounded retries."""
    boot = uuid4().hex
    while True:
        try:
            metadata = process_metadata(fence.settings, boot=boot)
            async with engine.connect() as connection:
                try:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(text("SET default_transaction_read_only = on"))
                    await connection.execute(text("SET statement_timeout = '3s'"))
                    await observe_connection(connection, fence=fence, metadata=metadata)
                finally:
                    # Never return session settings/application_name to the pool.
                    await connection.invalidate()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider/SQL exceptions can include connection details. No raw log.
            fence.last_observed = None
            logger.warning("Baseline process observation unavailable")
            await asyncio.sleep(5)
        finally:
            fence.last_observed = None


@asynccontextmanager
async def standalone_process_observation(
    *, engine: AsyncEngine, settings: Settings
) -> AsyncIterator[None]:
    """Reuse the web process observer or install one on the CLI worker engine."""
    existing = process_send_fence()
    if existing.observer_owned:
        if (
            existing.settings.database_url != settings.database_url
            or existing.settings.deployment_sha != settings.deployment_sha
            or existing.settings.gate_workspace_id != settings.gate_workspace_id
            or any(
                getattr(existing.settings, key) != getattr(settings, key) for key in _CONFIG_FIELDS
            )
        ):
            raise BaselineSendClosed("baseline_process_configuration_mismatch")
        if existing.last_observed is None or not 0 <= monotonic() - existing.last_observed <= 15:
            raise BaselineSendClosed("baseline_process_observation_unavailable")
        yield
        return
    fence = ProcessSendFence(settings, observations_required=True, observer_owned=True)
    previous = install_process_send_fence(fence)
    task = asyncio.create_task(run_baseline_observer(engine=engine, fence=fence))
    try:
        async with asyncio.timeout(4):
            while fence.last_observed is None:
                await asyncio.sleep(0.05)
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        install_process_send_fence(previous)


ACTIVITY_SQL = (
    "SELECT application_name, query, "
    "extract(epoch FROM clock_timestamp()-query_start) AS age, "
    "backend_start, query_start, xact_start, state, pid "
    "FROM pg_stat_activity WHERE application_name LIKE 'gate-g1a:%' LIMIT 129"
)


def validate_process_rows(
    rows: list[Mapping[str, object]],
    *,
    expected: list[Mapping[str, str]],
    identity: str,
) -> list[dict[str, str]]:
    """Validate a fresh snapshot against independently pinned provider processes.

    One expectation per actual web/worker OS process, not per replica. Repeated
    replicas are valid for multi-worker servers; boot/backend identity is unique.
    Caller still must prove no old/unmarked provider process remains active.
    """
    if not 0 < len(expected) <= 128 or len(rows) != len(expected):
        raise ValueError("fleet_cardinality_unknown")
    expected_keys = {"source", "replica", "deployment", "workspace", "config"}
    if any(set(item) != expected_keys for item in expected):
        raise ValueError("invalid_expectation")
    unmatched = [dict(item) for item in expected]
    result: list[dict[str, str]] = []
    boots: set[str] = set()
    backends: set[tuple[object, object]] = set()
    for row in rows:
        if not isinstance(row.get("query"), str):
            raise ValueError("process_visibility_unknown")
        fields = parse_marker(str(row["query"]))
        if row.get("application_name") != "gate-g1a:" + fields["boot"]:
            raise ValueError("process_binding_mismatch")
        age = row.get("age")
        if age is None or not 0 <= float(str(age)) <= 15:
            raise ValueError("process_observation_stale")
        if row.get("state") != "idle" or row.get("xact_start") is not None:
            raise ValueError("process_session_not_idle")
        if row.get("backend_start") is None or row.get("query_start") is None:
            raise ValueError("backend_identity_unknown")
        backend = (row.get("pid"), row["backend_start"])
        if backend[0] is None or fields["boot"] in boots or backend in backends:
            raise ValueError("duplicate_process")
        boots.add(fields["boot"])
        backends.add(backend)
        binding = {key: fields[key] for key in expected_keys}
        if binding not in unmatched or fields["identity"] != identity:
            raise ValueError("process_identity_mismatch")
        unmatched.remove(binding)
        if fields["send"] != "0" or fields["worker"] != "0" or fields["inflight"] != "0":
            raise ValueError("process_not_frozen")
        result.append(fields)
    return result


async def read_frozen_fleet(
    connection: AsyncConnection, *, expected: list[Mapping[str, str]]
) -> dict[str, object]:
    """Fixed read-only primitives for Ops. Does not authorize/configure/recover.

    Pass a fresh AUTOCOMMIT connection. Ops additionally owns TLS, complete role
    visibility, direct physical-session pool mode, provider inventory and lock.
    Returned counts are global/conservative; no message/history payload is read.
    """
    if connection.in_transaction():
        raise ValueError("fresh_connection_required")
    await connection.execution_options(isolation_level="AUTOCOMMIT")
    identity, _, _ = await read_identity(connection)
    rows = list((await connection.execute(text(ACTIVITY_SQL))).mappings().all())
    processes = validate_process_rows(
        [dict(row) for row in rows], expected=expected, identity=identity
    )
    counts = (
        await connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE status='reserved'), "
                "count(*) FILTER (WHERE status='sending'), "
                "count(*) FILTER (WHERE status='unknown') FROM send_attempts"
            )
        )
    ).one()
    reserved, sending, unknown = map(int, counts)
    return {
        "contract": "gate.baseline-fleet.v1",
        "result": "FROZEN_DRAINED" if reserved + sending + unknown == 0 else "BLOCKED_ATTEMPTS",
        "process_count": len(processes),
        "identity_sha256": identity,
        "reserved": reserved,
        "sending": sending,
        "unknown": unknown,
        "provider_inventory_verified": False,
        "backup_verified": False,
    }
