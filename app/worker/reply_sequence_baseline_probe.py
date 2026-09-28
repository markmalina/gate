"""Read-only pre-0112 database session probe for the sequence bootstrap.

This proves the CLI's own connection only. Ops must independently bind every
live Gate process to its actual session and inventory before a cutover.
"""

import argparse
import asyncio
import hashlib
import json
import re

from app.core.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

_SAFE_REF = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SAFE_REVISION = re.compile(r"[0-9]{4}_[a-z0-9_]+\Z")


def _required_ref(value: str) -> str:
    if not _SAFE_REF.fullmatch(value):
        raise ValueError("invalid_reference")
    return value


async def read_session_identity(connection: AsyncConnection) -> tuple[str, str]:
    """Read actual SQL session and Alembic ledger without schema 0112 tables."""

    result = await connection.execute(
        text(
            "SELECT current_database(), current_schema(), current_user, "
            "COALESCE(inet_server_addr()::text, 'local'), "
            "COALESCE(inet_server_port()::text, 'local')"
        )
    )
    identity = result.one()
    versions = (
        (await connection.execute(text("SELECT version_num FROM alembic_version")))
        .scalars()
        .all()
    )
    if len(versions) != 1 or not isinstance(versions[0], str):
        raise ValueError("invalid_schema_ledger")
    encoded = json.dumps(
        list(identity), separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest(), versions[0]


async def execute(
    *, operation_id: str, expected_workspace: str, expected_schema: str
) -> dict[str, object]:
    """Return a redacted, schema-bound CLI receipt; never create runtime/sender state."""

    _required_ref(operation_id)
    _required_ref(expected_workspace)
    if not _SAFE_REVISION.fullmatch(expected_schema):
        raise ValueError("invalid_schema_revision")
    settings = get_settings()
    if settings.gate_workspace_id != expected_workspace:
        raise ValueError("workspace_mismatch")
    if not settings.database_url:
        raise ValueError("database_binding_missing")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            digest, revision = await read_session_identity(connection)
    finally:
        await engine.dispose()
    return {
        "contract": "gate.sequence.baseline-probe.v1",
        "operation_id": operation_id,
        "workspace_ref": expected_workspace,
        "result": "READY" if revision == expected_schema else "BLOCKED_SCHEMA",
        "expected_schema": expected_schema,
        "observed_schema": revision
        if _SAFE_REVISION.fullmatch(revision)
        else "unknown",
        "cli_db_session_identity_sha256": digest,
        "runtime_processes_verified": False,
        "legacy_drained": False,
        "backup_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-workspace", required=True)
    parser.add_argument("--expected-schema", required=True)
    args = parser.parse_args()
    try:
        receipt = asyncio.run(
            execute(
                operation_id=args.operation_id,
                expected_workspace=args.expected_workspace,
                expected_schema=args.expected_schema,
            )
        )
    except Exception as exc:
        # SQLAlchemy/provider exceptions can embed a URL or server detail.
        # Log only the allowlisted local validation code, never the exception.
        code = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc)
            in {
                "invalid_reference",
                "invalid_schema_revision",
                "invalid_schema_ledger",
                "workspace_mismatch",
                "database_binding_missing",
            }
            else "probe_failed"
        )
        print(
            json.dumps(
                {
                    "contract": "gate.sequence.baseline-probe.v1",
                    "result": "BLOCKED",
                    "code": code,
                }
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(receipt, sort_keys=True))
    if receipt["result"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
