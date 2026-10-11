"""Fixed synthetic fixture backend. Public CLI has no trusted hosted adapter.

No provider, Settings, ordinary startup, arbitrary SQL/DSN/dataset flags, or
production predeploy is connected to this module. UNBOUND precedes env/DB.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.worker.gate745_fixture_policy import (
    BASELINE,
    GUARDED,
    PRODUCTION_IDS,
    Binding,
    ExecutionProof,
    FixtureDenied,
    Mode,
    TrustedFixtureExecutor,
    authorize,
    check_execution,
    check_runtime,
    digest,
    instant,
    load_binding,
)
from app.worker.gate745_fixture_recipe import (
    EMPTY_JOURNAL_DIGEST,
    JOURNAL_DIGEST,
    RECIPE_DIGEST,
    ROWS,
    canonical,
)


def target_digest(binding: Binding) -> str:
    value = {
        key: binding.target[key]
        for key in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    value["repository"] = "AlphaCenter/gate"
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _known_tables() -> set[str]:
    # Same fixed schema imports as Gate's Alembic environment, without Settings
    # or any worker/provider initialization. Historical schemas are data-free.
    from app.models import (  # noqa: F401
        agent,
        alpha,
        alpha_preflight,
        alpha_transcription,
        alpha_voice,
        aqub,
        aqub_live,
        become,
        become_learner_invitation,
        become_program_draft,
        become_quest,
        become_recovery,
        beta_context,
        beta_material,
        beta_space,
        beta_wedding_voice,
        billing,
        canonical,
        relationship_crm,
        watch_voice,
    )
    from app.models.domain import Base

    return set(Base.metadata.tables) - {"reply_dialog_versions"}


def _migrate(connection: Any) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).with_name("gate745_fixture_alembic"))
    )
    config.set_main_option("version_locations", str(root / "alembic" / "versions"))
    config.attributes["connection"] = connection
    command.upgrade(config, BASELINE)


def _identity(connection: Any, binding: Binding) -> None:
    from sqlalchemy import text

    if any(
        binding.target[key] in PRODUCTION_IDS
        for key in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    ):
        raise FixtureDenied("E_FIXTURE_PRODUCTION")
    # Actual physical PG identity, not just a caller-provided DB name/hash.
    row = (
        connection.execute(
            text(
                "SELECT current_database() AS name, inet_server_addr()::text AS address, "
                "inet_server_port() AS port, system_identifier::text AS system_identifier "
                "FROM pg_control_system()"
            )
        )
        .mappings()
        .one()
    )
    physical = {
        key: binding.target[key]
        for key in (
            "projectId",
            "environmentId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    physical.update(
        {
            "databaseName": row["name"],
            "serverAddress": row["address"],
            "serverPort": row["port"],
            "systemIdentifier": row["system_identifier"],
        }
    )
    if (
        row["name"] != "gate745_synthetic"
        or digest(physical) != binding.seed["databaseIdentityDigest"]
    ):
        raise FixtureDenied("E_FIXTURE_PHYSICAL_DATABASE")
    if (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN "
                "('public','pg_catalog','information_schema') AND nspname !~ '^pg_'"
            )
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_FOREIGN_SCHEMA")


def _tables(connection: Any, revision: str) -> Any:
    from sqlalchemy import BigInteger, Column, MetaData, String, Table, inspect, text

    expected = _known_tables() | {"alembic_version"}
    if revision == GUARDED:
        expected.add("reply_dialog_versions")
    inspector = inspect(connection)
    if (
        set(inspector.get_table_names(schema="public")) != expected
        or inspector.get_view_names(schema="public")
        or inspector.get_materialized_view_names(schema="public")
        or inspector.get_foreign_table_names(schema="public")
    ):
        raise FixtureDenied("E_FIXTURE_SCHEMA")
    if (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_SCHEMA")
    metadata = MetaData()
    metadata.reflect(bind=connection, schema="public")
    from app.models.domain import Base

    for table in metadata.sorted_tables:
        if table.name == "alembic_version":
            expected_table = Table(
                "alembic_version", MetaData(), Column("version_num", String(32), primary_key=True)
            )
        elif table.name == "reply_dialog_versions":
            expected_table = Table(
                "reply_dialog_versions",
                MetaData(),
                Column("business_connection_id", String(255), primary_key=True),
                Column("chat_id", String(255), primary_key=True),
                Column("revision", BigInteger(), nullable=False),
            )
        else:
            expected_table = Base.metadata.tables[table.name]
        if set(table.columns.keys()) != set(expected_table.columns.keys()) or set(
            table.primary_key.columns.keys()
        ) != set(expected_table.primary_key.columns.keys()):
            raise FixtureDenied("E_FIXTURE_SCHEMA")
        for column in table.columns:
            expected_column = expected_table.columns[column.name]
            if column.nullable != expected_column.nullable or str(
                column.type.compile(dialect=connection.dialect)
            ) != str(expected_column.type.compile(dialect=connection.dialect)):
                raise FixtureDenied("E_FIXTURE_SCHEMA")
        if table.name == "reply_dialog_versions":
            checks = inspector.get_check_constraints(table.name, schema="public")
            if (
                len(checks) != 1
                or checks[0]["name"] != "ck_reply_dialog_version_nonnegative"
                or re.sub(r"[()\s]", "", checks[0]["sqltext"]) != "revision>=0"
                or inspector.get_indexes(table.name, schema="public")
                or inspector.get_unique_constraints(table.name, schema="public")
            ):
                raise FixtureDenied("E_FIXTURE_SCHEMA")
    return metadata


def _row_values(table: Any) -> dict[str, object]:
    recipe = ROWS[table.name]
    values: dict[str, object] = {}
    for column in table.columns:
        if column.name in recipe:
            values[column.name] = recipe[column.name]
        elif column.nullable:
            values[column.name] = None
        else:
            raise FixtureDenied("E_FIXTURE_RECIPE_SCHEMA")
    if not set(recipe) <= set(values):
        raise FixtureDenied("E_FIXTURE_RECIPE_SCHEMA")
    return values


def _prove(connection: Any) -> tuple[str, str]:
    from sqlalchemy import func, select, text

    versions = list(
        connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars()
    )
    if len(versions) != 1 or versions[0] not in (BASELINE, GUARDED):
        raise FixtureDenied("E_FIXTURE_REVISION")
    revision = versions[0]
    metadata = _tables(connection, revision)
    # Count the whole fixed schema first; foreign rows never become payloads.
    for table in metadata.sorted_tables:
        if table.name == "alembic_version":
            continue
        count = connection.execute(select(func.count()).select_from(table)).scalar_one()
        if count != (1 if table.name in ROWS else 0):
            raise FixtureDenied("E_FIXTURE_NONLITERAL_STATE")
    for name in ROWS:
        table = metadata.tables[f"public.{name}"]
        row = dict(connection.execute(select(table)).mappings().one())
        if canonical(row) != canonical(_row_values(table)):
            raise FixtureDenied("E_FIXTURE_NONLITERAL_STATE")
    # All journal columns must be literal, including nullable provider/error
    # fields. The wire digest uses only the fixed recipe's journal keys.
    journal = metadata.tables["public.send_attempts"]
    row = dict(connection.execute(select(journal)).mappings().one())
    journal_digest = digest(canonical([{key: row[key] for key in ROWS["send_attempts"]}]))
    if journal_digest != JOURNAL_DIGEST:
        raise FixtureDenied("E_FIXTURE_UNKNOWN_JOURNAL")
    return revision, journal_digest


def _seed_empty(connection: Any) -> None:
    from sqlalchemy import func, select

    metadata = _tables(connection, BASELINE)
    for table in metadata.sorted_tables:
        if (
            table.name != "alembic_version"
            and connection.execute(select(func.count()).select_from(table)).scalar_one() != 0
        ):
            raise FixtureDenied("E_FIXTURE_SEED_NOT_EMPTY")
    for name in ROWS:  # Literal order follows the fixed FK recipe.
        table = metadata.tables[f"public.{name}"]
        connection.execute(table.insert().values(**_row_values(table)))


def _bootstrap_empty(connection: Any) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if (
        inspector.get_table_names(schema="public")
        or inspector.get_view_names(schema="public")
        or inspector.get_materialized_view_names(schema="public")
        or inspector.get_foreign_table_names(schema="public")
        or connection.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
        or connection.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_BOOTSTRAP_NOT_EMPTY")
    _migrate(connection)
    metadata = _tables(connection, BASELINE)
    # Fresh Gate migrations seed historical reference accounts/projects. Clear
    # only the schema just created from an authenticated EMPTY fixture DB,
    # never read/copy those rows and never use a production dump/restore.
    names = sorted(t.name for t in metadata.sorted_tables if t.name != "alembic_version")
    identifiers = ",".join(f'public."{name}"' for name in names)
    connection.execute(text("TRUNCATE TABLE " + identifiers + " CASCADE"))
    _seed_empty(connection)


def _operate(connection: Any, binding: Binding, proof: ExecutionProof) -> dict[str, object]:
    from sqlalchemy import text

    _identity(connection, binding)
    if (
        binding.seed["recipeDigest"] != RECIPE_DIGEST
        or binding.seed["unknownJournalDigest"] != JOURNAL_DIGEST
    ):
        raise FixtureDenied("E_FIXTURE_RECIPE_BINDING")
    before_count, before_digest = 0, EMPTY_JOURNAL_DIGEST
    if proof.mode == "bootstrap":
        _bootstrap_empty(connection)
    elif proof.mode == "seed":
        _seed_empty(connection)
    else:
        _, before_digest = _prove(connection)
        before_count = 1
        if proof.mode == "reset":
            # Quiescence/retained artifact/fresh reset authority must already
            # be authenticated by the executor. Never accepts SQL selectors.
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            _bootstrap_empty(connection)
    revision, after_digest = _prove(connection)
    return {
        "schemaRevision": revision,
        "reserved": 0,
        "sending": 0,
        "unknownCount": 1,
        "unknownJournalDigest": after_digest,
        "beforeUnknownCount": before_count,
        "beforeUnknownJournalDigest": before_digest,
    }


async def _execute_admitted(
    binding: Binding,
    executor: TrustedFixtureExecutor,
    proof: ExecutionProof,
) -> dict[str, object]:
    """Recheck installed binding before the credential/container boundary."""
    now = datetime.now(UTC)
    if binding != load_binding(now=now):
        raise FixtureDenied("E_FIXTURE_INSTALLED_BINDING")
    check_runtime(binding, os.environ.get)
    check_execution(binding, proof, proof.mode, now=now)
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = make_url(executor.database_url())
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != binding.target["databaseHost"]
        or url.database != binding.target["databaseName"]
        or url.username != binding.target["databaseUser"]
        or url.port != 5432
        or url.query
        or not url.password
    ):
        raise FixtureDenied("E_FIXTURE_DSN_BINDING")
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "timeout": 5,
            "server_settings": {
                "statement_timeout": "15000",
                "lock_timeout": "2000",
            },
        },
    )
    remaining = (instant(binding.authority["expiresAt"]) - datetime.now(UTC)).total_seconds()
    timeout = min(10 if proof.mode == "unknown-proof" else 120, remaining)
    if timeout <= 0:
        await engine.dispose()
        raise FixtureDenied("E_FIXTURE_EXPIRED")
    try:
        async with asyncio.timeout(timeout):
            async with engine.begin() as connection:
                if proof.mode == "unknown-proof":
                    from sqlalchemy import text

                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                state = await connection.run_sync(lambda conn: _operate(conn, binding, proof))
    finally:
        await engine.dispose()
    return {
        "schemaVersion": "gate745.fixture-bootstrap-receipt/v1",
        "mode": proof.mode,
        "result": "SUCCEEDED",
        **{
            key: binding.source[key]
            for key in (
                "helperSourceSha",
                "helperSourceTree",
                "currentSourceSha",
                "currentSourceTree",
                "setupRouteDigest",
                "proofRouteDigest",
            )
        },
        "helperManifestDigest": binding.manifest_digest,
        "recipeDigest": RECIPE_DIGEST,
        "databaseIdentityDigest": binding.seed["databaseIdentityDigest"],
        "targetDigest": target_digest(binding),
        "ownerGrantCommentId": proof.owner_grant_comment_id,
        "ownerGrantBodyDigest": proof.owner_grant_body_digest,
        "runId": proof.run_id,
        "runAttempt": proof.run_attempt,
        "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        **state,
        "seedDigest": RECIPE_DIGEST,
        "quiescenceReceiptDigest": proof.quiescence_receipt_digest,
        "retainedCaseReceiptDigest": proof.retained_case_receipt_digest,
        "resetGrantCommentId": proof.reset_grant_comment_id,
        "resetGrantBodyDigest": proof.reset_grant_body_digest,
        "applyPlan": False,
        "fixtureAcceptance": "NOT_PROVEN",
    }


async def run(mode: Mode) -> dict[str, object]:
    """Fixed CLI admission order. No trusted hosted route is installed."""
    now = datetime.now(UTC)
    binding = load_binding(now=now)
    check_runtime(binding, os.environ.get)
    # No command-line/env JSON/function can fabricate the missing executor.
    executor, proof = await authorize(mode, binding, None, now=now)
    return await _execute_admitted(binding, executor, proof)


async def fixture_proof(request: Any, authorization: str | None) -> dict[str, object]:
    """Fixed readonly endpoint admission, no Settings or shared admin secret."""
    from hmac import compare_digest

    now = datetime.now(UTC)
    binding = load_binding(now=now)
    check_runtime(binding, os.environ.get)
    executor, proof = await authorize(
        "unknown-proof",
        binding,
        getattr(request.app.state, "gate745_fixture_executor", None),
        now=now,
    )
    if binding != load_binding(now=datetime.now(UTC)):
        raise FixtureDenied("E_FIXTURE_INSTALLED_BINDING")
    check_runtime(binding, os.environ.get)
    # Dedicated server-side fixture binding may be read only after all control
    # admission. Current ordinary app never installs this executor.
    token = executor.fixture_admin_token()
    if (
        not token
        or not authorization
        or not authorization.startswith("Bearer ")
        or not compare_digest(authorization[7:], token)
    ):
        raise FixtureDenied("E_FIXTURE_ADMIN_AUTH")
    return await _execute_admitted(binding, executor, proof)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Default-off fixed Gate745 synthetic fixture helper"
    )
    parser.add_argument("mode", choices=("bootstrap", "seed", "reset", "unknown-proof"))
    args = parser.parse_args()
    try:
        result = asyncio.run(run(cast(Mode, args.mode)))
    except FixtureDenied as exc:
        print(
            json.dumps(
                {
                    "result": "DENIED",
                    "code": exc.code,
                    "fixtureAcceptance": "NOT_PROVEN",
                    "applyPlan": False,
                }
            )
        )
        raise SystemExit(1) from None
    except Exception:
        print(
            json.dumps(
                {
                    "result": "DENIED",
                    "code": "E_FIXTURE_EXECUTION",
                    "fixtureAcceptance": "NOT_PROVEN",
                    "applyPlan": False,
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
