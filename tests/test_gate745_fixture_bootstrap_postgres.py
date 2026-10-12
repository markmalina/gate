"""Real PG drill ONLY in the existing disposable CI service; never Railway.

Local absence is an explicit skip, not native fixture acceptance. The fixed
test-only DB is newly created, separately from CI's ordinary `gate` database.
"""

import asyncio
import hashlib
import importlib.util
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any

import pytest
from app.worker import gate745_fixture_bootstrap as helper
from app.worker.gate745_fixture_policy import BASELINE, GUARDED, FixtureDenied, digest
from app.worker.gate745_fixture_recipe import JOURNAL_DIGEST
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.test_gate745_fixture_bootstrap import NOW, admit_manifest, proof, raw_manifest

CI_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate"
TEST_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate745_synthetic"


@contextmanager
def _sealed_control_helper() -> Iterator[ModuleType]:
    """Test-only H119 artifact; never relabel the actual candidate120 as H."""
    root = Path(__file__).resolve().parents[1]
    source = root / "app/worker/gate745_fixture_bootstrap.py"
    with pytest.raises(FixtureDenied, match="E_FIXTURE_MIGRATION_ARTIFACT"):
        helper._migration_versions()
    with TemporaryDirectory(prefix="gate745-ci-h119-") as directory:
        artifact = Path(directory)
        worker = artifact / "app/worker"
        worker.mkdir(parents=True)
        shutil.copyfile(root / "pyproject.toml", artifact / "pyproject.toml")
        copied_module = worker / "gate745_fixture_bootstrap.py"
        copied_module.write_bytes(source.read_bytes())
        shutil.copytree(root / "app/worker/gate745_fixture_alembic",
                        worker / "gate745_fixture_alembic")
        versions = artifact / "alembic/versions"
        versions.mkdir(parents=True)
        assert len(helper.MIGRATION_SHA256) == 119
        for name, expected in helper.MIGRATION_SHA256.items():
            raw = (root / "alembic/versions" / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == expected
            (versions / name).write_bytes(raw)
        assert copied_module.read_bytes() == source.read_bytes()
        spec = importlib.util.spec_from_file_location("fixture_ci_h119_bootstrap", copied_module)
        assert spec is not None and spec.loader is not None
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        assert control._migration_versions() == versions
        assert control.MIGRATION_SHA256 == helper.MIGRATION_SHA256
        yield control
    with pytest.raises(FixtureDenied, match="E_FIXTURE_MIGRATION_ARTIFACT"):
        helper._migration_versions()


def _phase_helper(index: int, control: ModuleType) -> ModuleType:
    """Only lower-layer test source selection; not a runtime role assertion."""
    assert index in (1, 2, 3, 4, 5, 6)
    selected = helper if index in (2, 3, 5) else control
    assert (selected is helper) == (index in (2, 3, 5))
    return selected


def _upgrade_guarded_schema(connection: Any) -> None:
    """Execute the actual unchanged reviewed0116 migration, not copied DDL."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0116_reply_dialog_versions.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "7ff83d2032eaf87b0e9bfa45455aec6e99f3bdd020200688e4df2a2f723390ab"
    )
    spec = importlib.util.spec_from_file_location("fixture_actual_guard_0116", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == GUARDED and migration.down_revision == BASELINE
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    connection.execute(text("UPDATE alembic_version SET version_num='0116_reply_dialog_versions'"))


def test_real_pg_bootstrap_journal_denial_reset_and_0116_readonly() -> None:
    if os.environ.get("GATE_ENV") != "ci":
        pytest.skip("Requires existing isolated CI PostgreSQL service; no hosted DB access")
    if os.environ.get("DATABASE_URL") != CI_URL:
        pytest.skip("Exact checked-in disposable CI DB binding required")
    with _sealed_control_helper() as control:
        asyncio.run(_drill(control))


async def _drill(control_helper: ModuleType) -> None:
    admin = create_async_engine(CI_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    created = False
    engine = create_async_engine(TEST_URL, poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname='gate745_synthetic'")
            )
            if exists:
                pytest.fail("Refusing to reuse/drop an existing fixture database")
            await connection.execute(text("CREATE DATABASE gate745_synthetic"))
            created = True
        async with engine.begin() as connection:
            raw = await connection.execute(
                text(
                    "SELECT current_database() AS name, inet_server_addr()::text AS address, "
                    "inet_server_port() AS port, system_identifier::text AS system_identifier "
                    "FROM pg_control_system()"
                )
            )
            physical = raw.mappings().one()
            binding = admit_manifest(raw_manifest(), now=NOW)
            identity = {
                key: binding.target[key]
                for key in (
                    "projectId",
                    "environmentId",
                    "postgresServiceId",
                    "volumeInstanceId",
                )
            }
            identity.update(
                {
                    "databaseName": physical["name"],
                    "serverAddress": physical["address"],
                    "serverPort": physical["port"],
                    "systemIdentifier": physical["system_identifier"],
                }
            )
            binding = replace(
                binding, seed={**binding.seed, "databaseIdentityDigest": digest(identity)}
            )
            result = await connection.run_sync(
                lambda c: control_helper._operate(c, binding, proof("bootstrap"))
            )
            assert result["schemaRevision"] == BASELINE
            assert result["unknownCount"] == 1
            assert result["unknownJournalDigest"] == JOURNAL_DIGEST
            assert result["beforeUnknownCount"] == 0
            # Actual migrations' default historical accounts/projects were
            # removed: no historical account, active identity, token or data.
            assert await connection.scalar(text("SELECT count(*) FROM accounts")) == 1
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM accounts WHERE is_active OR read_enabled "
                        "OR send_enabled OR telegram_user_id IS NOT NULL"
                    )
                )
                == 0
            )
        async with engine.begin() as connection:
            # Canonical unqualified FLOAT is physically DOUBLE PRECISION.
            # Wrong precision and nullability must still deny before reset.
            await connection.execute(
                text("ALTER TABLE conversation_context_bindings ALTER COLUMN confidence TYPE REAL")
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: control_helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE conversation_context_bindings ALTER COLUMN confidence "
                    "TYPE DOUBLE PRECISION"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE conversation_context_bindings ALTER COLUMN confidence SET NOT NULL"
                )
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: control_helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE conversation_context_bindings "
                    "ALTER COLUMN confidence DROP NOT NULL"
                )
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_BOOTSTRAP_NOT_EMPTY"):
                await connection.run_sync(
                    lambda c: control_helper._operate(c, binding, proof("bootstrap"))
                )
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE send_attempts SET status='sending'"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_NONLITERAL_STATE"):
                await connection.run_sync(lambda c: control_helper._operate(c, binding, proof()))
            with pytest.raises(FixtureDenied, match="E_FIXTURE_NONLITERAL_STATE"):
                await connection.run_sync(
                    lambda c: control_helper._operate(c, binding, proof("reset"))
                )
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE send_attempts SET status='unknown'"))
            await connection.run_sync(_upgrade_guarded_schema)
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            observed = await connection.run_sync(
                lambda c: control_helper._operate(c, binding, proof())
            )
            assert observed["schemaRevision"] == GUARDED
            assert (
                observed["beforeUnknownJournalDigest"]
                == observed["unknownJournalDigest"]
                == JOURNAL_DIGEST
            )
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE send_attempts ADD COLUMN unexpected text"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: control_helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE send_attempts DROP COLUMN unexpected"))
            await connection.execute(text("CREATE SCHEMA contaminated"))
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_FOREIGN_SCHEMA"):
                await connection.run_sync(
                    lambda c: control_helper._operate(c, binding, proof("reset"))
                )
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA contaminated"))
            await connection.execute(
                text(
                    "ALTER TABLE reply_dialog_versions DROP CONSTRAINT "
                    "ck_reply_dialog_version_nonnegative"
                )
            )
        async with engine.begin() as connection:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                await connection.run_sync(lambda c: control_helper._operate(c, binding, proof()))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE reply_dialog_versions ADD CONSTRAINT "
                    "ck_reply_dialog_version_nonnegative CHECK(revision>=0)"
                )
            )
        async with engine.begin() as connection:
            fresh_reset = replace(
                proof("reset"),
                reset_grant_comment_id="1003",
                reset_grant_body_digest="sha256:" + "a" * 64,
                retained_case_receipt_digest="sha256:" + "b" * 64,
                quiescence_receipt_digest="sha256:" + "c" * 64,
            )
            restored: dict[str, Any] = await connection.run_sync(
                lambda c: control_helper._operate(c, binding, fresh_reset)
            )
            assert restored["schemaRevision"] == BASELINE
            assert restored["beforeUnknownCount"] == restored["unknownCount"] == 1
            assert (
                restored["beforeUnknownJournalDigest"]
                == restored["unknownJournalDigest"]
                == JOURNAL_DIGEST
            )
        await _hosted_ledger_drill(engine, binding, control_helper)
    finally:
        await engine.dispose()
        if created:
            async with admin.connect() as connection:
                await connection.execute(text("DROP DATABASE gate745_synthetic"))
        await admin.dispose()


async def _hosted_ledger_drill(
    engine: Any, physical_binding: Any, control_helper: ModuleType
) -> None:
    """Continue the SAME disposable CI DB; no second DB or hosted route.

    Synthetic RSA admission is intentionally separate from authentic C0/native
    acceptance. This exercise verifies actual PG commits, locks, schema, lineage,
    public reset and consumption on failure; it never issues a real GH token.
    """
    import json

    from app.worker import gate745_fixture_hosted as hosted
    from app.worker import gate745_fixture_ledger as ledger
    from tests.test_gate745_fixture_hosted import admitted, bundle

    b = bundle()
    m = json.loads(b["manifestRaw"])
    m["binding"]["seed"]["databaseIdentityDigest"] = physical_binding.seed["databaseIdentityDigest"]
    m["binding"]["seed"]["helperManifestDigest"] = hosted.source_scope_digest(m["binding"])
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    prior = None
    final_operation = None
    for index in range(1, 7):
        phase_helper = _phase_helper(index, control_helper)
        if index in (2, 5):
            async with engine.begin() as connection:
                await connection.run_sync(_upgrade_guarded_schema)
        operation = admitted(index, b=b, prior=prior)
        async with engine.connect() as connection:
            async with connection.begin():
                assert await connection.scalar(text("SELECT pg_try_advisory_lock(7457452026)"))
                await connection.run_sync(lambda c, op=operation: ledger.reserve(c, op))
            # Another connection observes the committed RESERVED claim and
            # cannot obtain the effect lock, proving cross-session exclusion.
            async with engine.connect() as concurrent:
                assert not await concurrent.scalar(text("SELECT pg_try_advisory_lock(7457452026)"))
                assert (
                    await concurrent.scalar(
                        text(
                            "SELECT state FROM gate745_fixture_control.operation_ledger "
                            "WHERE operation_index=:index"
                        ),
                        {"index": index},
                    )
                    == "RESERVED"
                )
            async with connection.begin():
                if operation.proof.mode == "unknown-proof":
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                state = await connection.run_sync(
                    lambda c, op=operation, phase=phase_helper: phase._operate(
                        c, op.binding, op.proof, hosted=op
                    )
                )
                assert state["unknownCount"] == 1
                assert state["unknownJournalDigest"] == JOURNAL_DIGEST
                assert state["schemaRevision"] == (BASELINE if index in (1, 4) else GUARDED)
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM gate745_fixture_control.operation_ledger")
                    )
                    == index
                )  # public reset retained the durable control schema.
            receipt = ledger.receipt_for(
                operation,
                state,
                execution_role=(
                    "CONTROL" if index in (1, 4) else "PROOF_ONLY" if index == 6 else "GUARDED"
                ),
            )
            async with connection.begin():
                await connection.run_sync(
                    lambda c, op=operation, r=receipt: ledger.finish(c, op, r)
                )
                prior = await connection.scalar(
                    text(
                        "SELECT completion_digest FROM gate745_fixture_control.operation_ledger "
                        "WHERE operation_index=:index"
                    ),
                    {"index": index},
                )
            async with connection.begin():
                with pytest.raises(FixtureDenied):
                    await connection.run_sync(lambda c, op=operation: ledger.reserve(c, op))
            final_operation = operation
    assert final_operation is not None
    async with engine.begin() as connection:
        # The old v1 path still rejects even this exact reviewed control schema.
        with pytest.raises(FixtureDenied, match="E_FIXTURE_FOREIGN_SCHEMA"):
            await connection.run_sync(lambda c: helper._operate(c, physical_binding, proof()))
        await connection.execute(
            text(
                "ALTER TABLE gate745_fixture_control.operation_ledger ADD COLUMN foreign_value text"
            )
        )
    async with engine.begin() as connection:
        with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_CONTROL_SCHEMA"):
            await connection.run_sync(lambda c: ledger.validate_control_schema(c))
    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE gate745_fixture_control.operation_ledger DROP COLUMN foreign_value")
        )
    for change, restore in (
        ("ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY"),
        ("SET UNLOGGED", "SET LOGGED"),
    ):
        async with engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE gate745_fixture_control.operation_ledger " + change)
            )
            with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_CONTROL_SCHEMA"):
                await connection.run_sync(lambda c: ledger.validate_control_schema(c))
            await connection.execute(
                text("ALTER TABLE gate745_fixture_control.operation_ledger " + restore)
            )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE DOMAIN gate745_fixture_control.foreign_type AS text"))
        with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_CONTROL_SCHEMA"):
            await connection.run_sync(lambda c: ledger.validate_control_schema(c))
        await connection.execute(text("DROP DOMAIN gate745_fixture_control.foreign_type"))
        await connection.run_sync(lambda c: ledger.validate_control_schema(c))
        # Model a committed reservation whose effect rolled back. UNKNOWN is
        # durably retained even though the failed write itself is absent.
        await connection.execute(
            text(
                "UPDATE gate745_fixture_control.operation_ledger "
                "SET state='RESERVED',"
                "receipt_digest=NULL,completion_digest=NULL WHERE operation_index=6"
            )
        )
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE send_attempts SET status='sending'"))
            raise RuntimeError("synthetic rollback")
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: ledger.finish(c, final_operation, None))
    async with engine.begin() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT state FROM gate745_fixture_control.operation_ledger "
                    "WHERE operation_index=6"
                )
            )
            == "UNKNOWN"
        )
        assert await connection.scalar(text("SELECT status FROM send_attempts")) == "unknown"
        with pytest.raises(FixtureDenied):
            await connection.run_sync(lambda c: ledger.reserve(c, final_operation))
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM gate745_fixture_control.operation_ledger")
            )
            == 6
        )
