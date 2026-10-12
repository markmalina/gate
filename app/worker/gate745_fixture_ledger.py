"""One fixed synthetic PG control table; durable consumption precedes effect.

The session lock spans the reservation commit and effect transaction. A
rollback, lost HTTP response, process death or unavailable completion never
releases an operation. RESERVED and UNKNOWN poison the original B permanently.
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.worker.gate745_fixture_hosted import (
    OPERATIONS,
    HostedOperation,
    assert_admitted,
    raw_digest,
    wire_json,
)
from app.worker.gate745_fixture_policy import DIGEST, FixtureDenied, check_runtime, instant

SCHEMA = "gate745_fixture_control"
TABLE = "operation_ledger"
LOCK = 7457452026
DDL = """CREATE SCHEMA gate745_fixture_control;
CREATE TABLE gate745_fixture_control.operation_ledger (
 operation_index INTEGER PRIMARY KEY,
 binding_digest VARCHAR(71) NOT NULL,
 run_id VARCHAR(20) NOT NULL,
 mode VARCHAR(16) NOT NULL,
 jti_digest VARCHAR(71) NOT NULL UNIQUE,
 reservation_digest VARCHAR(71) NOT NULL UNIQUE,
 state VARCHAR(9) NOT NULL,
 prior_completion_digest VARCHAR(71),
 receipt_digest VARCHAR(71),
 completion_digest VARCHAR(71),
 source_sha VARCHAR(40) NOT NULL,
 observed_at TIMESTAMP WITH TIME ZONE NOT NULL
)"""
DDL_DIGEST = raw_digest(DDL)
COLUMNS = {
    "operation_index": ("INTEGER", False),
    "binding_digest": ("VARCHAR(71)", False),
    "run_id": ("VARCHAR(20)", False),
    "mode": ("VARCHAR(16)", False),
    "jti_digest": ("VARCHAR(71)", False),
    "reservation_digest": ("VARCHAR(71)", False),
    "state": ("VARCHAR(9)", False),
    "prior_completion_digest": ("VARCHAR(71)", True),
    "receipt_digest": ("VARCHAR(71)", True),
    "completion_digest": ("VARCHAR(71)", True),
    "source_sha": ("VARCHAR(40)", False),
    "observed_at": ("TIMESTAMP WITH TIME ZONE", False),
}


def validate_control_schema(connection: Any, *, allow_missing: bool = False) -> bool:
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if SCHEMA not in inspector.get_schema_names():
        if allow_missing:
            return False
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    if (
        inspector.get_table_names(schema=SCHEMA) != [TABLE]
        or inspector.get_view_names(schema=SCHEMA)
        or inspector.get_materialized_view_names(schema=SCHEMA)
        or inspector.get_foreign_table_names(schema=SCHEMA)
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    canonical_table = connection.scalar(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='gate745_fixture_control' AND c.relname='operation_ledger' "
            "AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity "
            "AND NOT c.relforcerowsecurity AND NOT c.relispartition AND c.reloftype=0 "
            "AND c.relreplident='d' AND c.reloptions IS NULL"
        )
    )
    if canonical_table != 1:
        # RLS can hide consumed rows; UNLOGGED loses them after a crash.
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    columns = inspector.get_columns(TABLE, schema=SCHEMA)
    if {c["name"] for c in columns} != set(COLUMNS) or len(columns) != len(COLUMNS):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    for column in columns:
        expected_type, nullable = COLUMNS[column["name"]]
        if (
            column["type"].compile(dialect=connection.dialect) != expected_type
            or column["nullable"] is not nullable
            or column.get("default") is not None
            or column.get("identity") is not None
            or column.get("computed") is not None
        ):
            raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    pk = inspector.get_pk_constraint(TABLE, schema=SCHEMA)
    unique = inspector.get_unique_constraints(TABLE, schema=SCHEMA)
    if (
        pk.get("constrained_columns") != ["operation_index"]
        or len(unique) != 2
        or {tuple(x["column_names"]) for x in unique} != {("jti_digest",), ("reservation_digest",)}
        or inspector.get_foreign_keys(TABLE, schema=SCHEMA)
        or inspector.get_check_constraints(TABLE, schema=SCHEMA)
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    indices = inspector.get_indexes(TABLE, schema=SCHEMA)
    if len(indices) != 2 or any(
        not x.get("unique")
        or x.get("duplicates_constraint") != x["name"]
        or x["column_names"] not in (["jti_digest"], ["reservation_digest"])
        for x in indices
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    constraints = (
        connection.execute(
            text(
                "SELECT c.conname,c.condeferrable,c.condeferred,c.convalidated,"
                "pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
                "JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace "
                "WHERE n.nspname='gate745_fixture_control' AND r.relname='operation_ledger'"
            )
        )
        .mappings()
        .all()
    )
    expected_constraints = {
        "operation_ledger_pkey": "PRIMARY KEY (operation_index)",
        "operation_ledger_jti_digest_key": "UNIQUE (jti_digest)",
        "operation_ledger_reservation_digest_key": "UNIQUE (reservation_digest)",
    }
    if len(constraints) != 3 or any(
        expected_constraints.get(row["conname"]) != row["definition"]
        or row["condeferrable"]
        or row["condeferred"]
        or not row["convalidated"]
        for row in constraints
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    if (
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='gate745_fixture_control'"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='gate745_fixture_control' AND c.relkind NOT IN ('r','i')"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='gate745_fixture_control' AND NOT t.tgisinternal"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='gate745_fixture_control'"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='gate745_fixture_control'"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_inherits i JOIN pg_class c "
                "ON c.oid=i.inhrelid OR c.oid=i.inhparent "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='gate745_fixture_control'"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_depend d JOIN pg_namespace n "
                "ON d.refclassid='pg_namespace'::regclass AND d.refobjid=n.oid "
                "WHERE n.nspname='gate745_fixture_control' AND NOT ("
                "(d.classid='pg_class'::regclass AND d.objid IN (SELECT c.oid "
                "FROM pg_class c WHERE c.relnamespace=n.oid AND c.relname IN "
                "('operation_ledger','operation_ledger_pkey','operation_ledger_jti_digest_key',"
                "'operation_ledger_reservation_digest_key'))) OR "
                "(d.classid='pg_type'::regclass AND d.objid IN (SELECT t.oid "
                "FROM pg_type t WHERE t.typnamespace=n.oid AND t.typname IN "
                "('operation_ledger','_operation_ledger'))) OR "
                "(d.classid='pg_constraint'::regclass AND d.objid IN (SELECT c.oid "
                "FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid "
                "WHERE c.connamespace=n.oid AND r.relnamespace=n.oid "
                "AND r.relname='operation_ledger' AND c.conname IN "
                "('operation_ledger_pkey','operation_ledger_jti_digest_key',"
                "'operation_ledger_reservation_digest_key'))))"
            )
        )
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONTROL_SCHEMA")
    return True


def reserve(connection: Any, operation: HostedOperation) -> None:
    from sqlalchemy import text

    from app.worker import gate745_fixture_bootstrap as helper

    assert_admitted(operation)
    helper._identity(connection, operation.binding, hosted=operation)
    exists = validate_control_schema(
        connection, allow_missing=operation.proof.total_invocations == 1
    )
    if not exists:
        # Exactly one fixed creation, under authenticated index1 authority.
        connection.execute(text("CREATE SCHEMA gate745_fixture_control"))
        connection.execute(text(DDL.split(";\n", 1)[1]))
        validate_control_schema(connection)
    rows = (
        connection.execute(
            text(
                "SELECT * FROM gate745_fixture_control.operation_ledger "
                "ORDER BY operation_index LIMIT 8"
            )
        )
        .mappings()
        .all()
    )
    record = operation.reservation
    if len(rows) > 7 or len(rows) + 1 != record["index"] or record["mode"] != OPERATIONS[len(rows)]:
        raise FixtureDenied("E_FIXTURE_V2_LEDGER_ORDER")
    for i, row in enumerate(rows, 1):
        if (
            row["operation_index"] != i
            or row["binding_digest"] != operation.binding.manifest_digest
            or row["run_id"] != operation.proof.run_id
            or row["mode"] != OPERATIONS[i - 1]
            or row["state"] != "COMPLETED"
            or not all(
                isinstance(row[k], str) and DIGEST.fullmatch(row[k])
                for k in ("jti_digest", "reservation_digest", "receipt_digest", "completion_digest")
            )
        ):
            raise FixtureDenied("E_FIXTURE_V2_LEDGER_UNKNOWN")
        if (
            row["jti_digest"] == operation.jti_digest
            or row["reservation_digest"] == operation.reservation_digest
        ):
            raise FixtureDenied("E_FIXTURE_V2_LEDGER_REPLAY")
    prior = rows[-1]["completion_digest"] if rows else None
    if record["priorCompletionDigest"] != prior:
        raise FixtureDenied("E_FIXTURE_V2_LEDGER_LINEAGE")
    if record["mode"] == "reset" and (len(rows) != 3 or not record["retained"]):
        raise FixtureDenied("E_FIXTURE_V2_RESET_LINEAGE")
    connection.execute(
        text(
            "INSERT INTO gate745_fixture_control.operation_ledger "
            "(operation_index,binding_digest,run_id,mode,jti_digest,reservation_digest,state,"
            "prior_completion_digest,source_sha,observed_at) "
            "VALUES (:index,:binding,:run,:mode,:jti,:reservation,"
            "'RESERVED',:prior,:source,:observed)"
        ),
        {
            "index": record["index"],
            "binding": operation.binding.manifest_digest,
            "run": operation.proof.run_id,
            "mode": record["mode"],
            "jti": operation.jti_digest,
            "reservation": operation.reservation_digest,
            "prior": prior,
            "source": operation.proof.source_sha,
            "observed": datetime.now(UTC),
        },
    )


def finish(connection: Any, operation: HostedOperation, receipt: dict[str, object] | None) -> None:
    from sqlalchemy import text

    assert_admitted(operation)
    receipt_digest = raw_digest(wire_json(receipt)) if receipt is not None else None
    completed = {
        **operation.reservation,
        "state": "COMPLETED",
        "reservationDigest": operation.reservation_digest,
        "receiptDigest": receipt_digest,
    }
    completion = raw_digest(wire_json(completed)) if receipt is not None else None
    updated = connection.execute(
        text(
            "UPDATE gate745_fixture_control.operation_ledger SET state=:state,"
            "receipt_digest=:receipt,completion_digest=:completion WHERE operation_index=:index "
            "AND binding_digest=:binding AND jti_digest=:jti AND state='RESERVED'"
        ),
        {
            "state": "COMPLETED" if receipt is not None else "UNKNOWN",
            "receipt": receipt_digest,
            "completion": completion,
            "index": operation.reservation["index"],
            "binding": operation.binding.manifest_digest,
            "jti": operation.jti_digest,
        },
    )
    if updated.rowcount != 1:
        raise FixtureDenied("E_FIXTURE_V2_LEDGER_UNKNOWN")


def receipt_for(
    operation: HostedOperation, state: dict[str, object], *, execution_role: str
) -> dict[str, object]:
    binding, proof = operation.binding, operation.proof
    retained = operation.reservation["retained"] or {}
    from app.worker.gate745_fixture_bootstrap import target_digest

    return {
        "schemaVersion": "gate745.fixture-bootstrap-receipt/v2",
        "mode": proof.mode,
        "result": "SUCCEEDED",
        "executionRole": execution_role,
        **dict(binding.source),
        "helperManifestDigest": json.loads(operation.bundle["manifestRaw"])["binding"]["seed"][
            "helperManifestDigest"
        ],
        "runBindingDigest": binding.manifest_digest,
        "reservationDigest": operation.reservation_digest,
        "operationIndex": operation.reservation["index"],
        "recipeDigest": binding.seed["recipeDigest"],
        "databaseIdentityDigest": binding.seed["databaseIdentityDigest"],
        "targetDigest": target_digest(binding),
        "ownerGrantCommentId": proof.owner_grant_comment_id,
        "ownerGrantBodyDigest": proof.owner_grant_body_digest,
        "runId": proof.run_id,
        "runAttempt": 1,
        "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        **state,
        "seedDigest": binding.seed["recipeDigest"],
        "controlLedgerDdlDigest": DDL_DIGEST,
        "quiescenceReceiptDigest": retained.get("quiescenceDigest"),
        "retainedCaseReceiptDigest": retained.get("successDigest"),
        "retainedLiveReceiptDigest": retained.get("liveDigest"),
        "conditionalReset": proof.mode == "reset",
        "resetGrantCommentId": None,
        "resetGrantBodyDigest": None,
        "applyPlan": False,
        "fixtureAcceptance": "NOT_PROVEN",
    }


async def execute_hosted(
    operation: HostedOperation, *, execution_role: str = "GUARDED"
) -> dict[str, object]:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.worker import gate745_fixture_bootstrap as helper

    assert_admitted(operation)
    expected_role = (
        "CONTROL"
        if operation.reservation["index"] in (1, 4)
        else "PROOF_ONLY"
        if operation.reservation["index"] == 6
        else "GUARDED"
    )
    if execution_role != expected_role:
        raise FixtureDenied("E_FIXTURE_V2_EXECUTION_ROLE")
    now = datetime.now(UTC)
    if (
        now >= instant(operation.binding.authority["expiresAt"])
        or now.timestamp() >= operation.jwt_expires
    ):
        raise FixtureDenied("E_FIXTURE_EXPIRED")
    check_runtime(operation.binding, os.environ.get)
    deadline = instant(operation.binding.authority["expiresAt"])
    if operation.proof.mode != "unknown-proof":
        deadline = min(
            deadline, instant(operation.binding.authority["createdAt"]) + timedelta(minutes=100)
        )
        if now >= deadline:
            raise FixtureDenied("E_FIXTURE_NEW_TEST_DEADLINE")
    # Complete C0 effect admission and fixed runtime precede credential access.
    url = make_url(os.environ.get("GATE745_FIXTURE_DATABASE_URL", ""))
    target = operation.binding.target
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != target["databaseHost"]
        or url.port != 5432
        or url.database != target["databaseName"]
        or url.username != target["databaseUser"]
        or not url.password
        or url.query
    ):
        raise FixtureDenied("E_FIXTURE_DSN_BINDING")
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "timeout": 5,
            "server_settings": {"statement_timeout": "15000", "lock_timeout": "2000"},
        },
    )
    remaining = min(
        (deadline - now).total_seconds(),
        operation.jwt_expires - now.timestamp(),
    )
    timeout = min(10 if operation.proof.mode == "unknown-proof" else 120, remaining)
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as connection:
                async with connection.begin():
                    if not await connection.scalar(text("SELECT pg_try_advisory_lock(7457452026)")):
                        raise FixtureDenied("E_FIXTURE_V2_LEDGER_BUSY")
                    await connection.run_sync(lambda c: reserve(c, operation))
                # RESERVED is now committed. Every later failure consumes it.
                try:
                    async with connection.begin():
                        if operation.proof.mode == "unknown-proof":
                            await connection.execute(text("SET TRANSACTION READ ONLY"))
                        state = await connection.run_sync(
                            lambda c: helper._operate(
                                c, operation.binding, operation.proof, hosted=operation
                            )
                        )
                    receipt = receipt_for(operation, state, execution_role=execution_role)
                    async with connection.begin():
                        await connection.run_sync(lambda c: finish(c, operation, receipt))
                    return receipt
                except BaseException:
                    # Connection/process loss may leave RESERVED. That is also
                    # consumed uncertainty, never permission to retry.
                    if not connection.invalidated:
                        try:
                            async with asyncio.timeout(2), connection.begin():
                                await connection.run_sync(lambda c: finish(c, operation, None))
                        except BaseException:
                            pass
                    raise
    finally:
        await engine.dispose()
