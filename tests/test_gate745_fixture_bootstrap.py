"""Hermetic admission/endpoint tests; no hosted/native acceptance is claimed."""

import asyncio
import copy
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.api.health_routes import gate745_fixture_proof
from app.worker import gate745_fixture_bootstrap as helper
from app.worker import gate745_fixture_policy as policy
from app.worker.gate745_fixture_manifest import MANIFEST_JSON
from app.worker.gate745_fixture_policy import (
    BASELINE,
    ENVIRONMENT,
    MODE_CAPS,
    PRODUCTION_IDS,
    WORKFLOW,
    ExecutionProof,
    FixtureDenied,
    TrustedFixtureExecutor,
    admit_manifest,
    authorize,
    check_execution,
    check_runtime,
    digest,
)
from app.worker.gate745_fixture_recipe import JOURNAL_DIGEST, RECIPE_DIGEST, ROWS, canonical
from fastapi import FastAPI, Request

NOW = datetime(2026, 10, 11, 14, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def manifest() -> dict[str, Any]:
    """Explicit simulated bindings. These are never installed or native IDs."""
    return {
        "schemaVersion": "gate745.fixture-bootstrap-manifest/v1",
        "state": "BOUND",
        "enabled": True,
        "binding": {
            "target": {
                "projectId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c211",
                "environmentId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c212",
                "serviceId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c213",
                "postgresServiceId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c214",
                "volumeInstanceId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c215",
                "databaseHost": "gate745-synthetic-postgres.railway.internal",
                "databaseName": "gate745_synthetic",
                "databaseUser": "gate745_synthetic",
                "healthUrl": "https://gate745-synthetic.up.railway.app/admin/ops/gate745-fixture-proof",
            },
            "source": {
                "helperSourceSha": "a" * 40,
                "helperSourceTree": "b" * 40,
                "currentSourceSha": "c" * 40,
                "currentSourceTree": "d" * 40,
                "reviewIssue": 300,
                "reviewCommentId": "1001",
                "reviewBodyDigest": HASH,
                "setupRouteDigest": HASH,
                "proofRouteDigest": HASH,
            },
            "authority": {
                "ownerId": 23397294,
                "controlIssue": 745,
                "workflowPath": WORKFLOW,
                "protectedEnvironment": ENVIRONMENT,
                "ownerGrantCommentId": "1002",
                "ownerGrantBodyDigest": HASH,
                "createdAt": "2026-10-11T14:00:00.000Z",
                "expiresAt": "2026-10-11T16:00:00.000Z",
            },
            "seed": {
                "baselineRevision": BASELINE,
                "recipeDigest": RECIPE_DIGEST,
                "databaseIdentityDigest": HASH,
                "unknownJournalDigest": JOURNAL_DIGEST,
                "expectedUnknown": 1,
            },
            "caps": {
                "helperDeploys": 2,
                "pgSetupDeploys": 1,
                "modeInvocations": dict(MODE_CAPS),
                "totalInvocations": 7,
            },
        },
    }


def raw_manifest(value: dict[str, Any] | None = None) -> bytes:
    return json.dumps(value or manifest(), sort_keys=True).encode()


def proof(mode: Any = "unknown-proof") -> ExecutionProof:
    b = admit_manifest(raw_manifest(), now=NOW)
    return ExecutionProof(
        binding_digest=b.manifest_digest,
        source_sha="c" * 40,
        source_tree="d" * 40,
        owner_grant_comment_id="1002",
        owner_grant_body_digest=HASH,
        workflow_path=WORKFLOW,
        protected_environment=ENVIRONMENT,
        run_id="12345",
        run_attempt=1,
        mode=mode,
        invocation=1,
        total_invocations=1,
    )


def request() -> Request:
    return Request(
        {
            "type": "http",
            "app": FastAPI(),
            "method": "GET",
            "path": "/admin/ops/gate745-fixture-proof",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
        }
    )


class PoisonState:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"credential/container/DB state accessed: {name}")


class ExplodingEnvironment:
    def get(self, key: str) -> str:
        raise AssertionError(f"environment access before admission: {key}")


@pytest.mark.parametrize("mode", list(MODE_CAPS))
def test_unbound_denies_all_modes_before_environment(
    monkeypatch: pytest.MonkeyPatch, mode: Any
) -> None:
    monkeypatch.setattr(helper, "os", SimpleNamespace(environ=ExplodingEnvironment()))
    with pytest.raises(FixtureDenied, match="E_FIXTURE_UNBOUND"):
        asyncio.run(helper.run(mode))


def test_authenticated_endpoint_still_denies_before_env_credentials_or_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helper, "os", SimpleNamespace(environ=ExplodingEnvironment()))
    req = request()
    req.scope["app"] = SimpleNamespace(state=PoisonState())
    response = asyncio.run(gate745_fixture_proof(req, "Bearer synthetic-fixture-admin"))
    assert response.status_code == 404
    assert json.loads(response.body) == {"detail": "Not Found"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "slot",
    [
        "projectId",
        "environmentId",
        "serviceId",
        "postgresServiceId",
        "volumeInstanceId",
    ],
)
def test_bound_production_endpoint_denies_before_env_or_container(
    monkeypatch: pytest.MonkeyPatch,
    slot: str,
) -> None:
    value = manifest()
    value["binding"]["target"][slot] = sorted(PRODUCTION_IDS)[0]
    monkeypatch.setattr(policy, "MANIFEST_JSON", raw_manifest(value))
    monkeypatch.setattr(helper, "os", SimpleNamespace(environ=ExplodingEnvironment()))
    req = request()
    req.scope["app"] = SimpleNamespace(state=PoisonState())
    assert asyncio.run(gate745_fixture_proof(req, "Bearer synthetic-admin")).status_code == 404


def test_wrong_authenticated_source_proof_cannot_read_fixture_admin_or_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticated: list[str] = []

    class WrongSourceExecutor(TrustedFixtureExecutor):
        async def authenticate(self, mode: Any, binding: Any) -> ExecutionProof:
            authenticated.append(mode)
            return replace(proof(), source_sha="e" * 40)

        def database_url(self) -> str:
            raise AssertionError("DB credential read after wrong source proof")

        def fixture_admin_token(self) -> str:
            raise AssertionError("admin credential read after wrong source proof")

    value = manifest()
    monkeypatch.setattr(helper, "datetime", SimpleNamespace(now=lambda tz: NOW))
    monkeypatch.setattr(policy, "MANIFEST_JSON", raw_manifest(value))
    target = value["binding"]["target"]
    controls = {
        "RAILWAY_PROJECT_ID": target["projectId"],
        "RAILWAY_ENVIRONMENT_ID": target["environmentId"],
        "RAILWAY_SERVICE_ID": target["serviceId"],
        "RAILWAY_GIT_COMMIT_SHA": "c" * 40,
    }
    monkeypatch.setattr(helper, "os", SimpleNamespace(environ=controls))
    req = request()
    req.app.state.gate745_fixture_executor = WrongSourceExecutor()
    assert asyncio.run(gate745_fixture_proof(req, "Bearer synthetic-admin")).status_code == 404
    assert authenticated == ["unknown-proof"]


@pytest.mark.parametrize(
    "slot", ["projectId", "environmentId", "serviceId", "postgresServiceId", "volumeInstanceId"]
)
@pytest.mark.parametrize("production_id", sorted(PRODUCTION_IDS))
def test_every_production_id_is_denied_in_every_target_slot(slot: str, production_id: str) -> None:
    value = manifest()
    value["binding"]["target"][slot] = production_id
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PRODUCTION"):
        admit_manifest(raw_manifest(value), now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "healthUrl": "https://gate-production-bab1.up.railway.app/admin/ops/gate745-fixture-proof"
        },
        {"databaseHost": "postgres.railway.internal"},
        {"databaseName": "gate"},
        {"databaseUser": "postgres"},
        {
            "healthUrl": "https://gate745-synthetic.up.railway.app/admin/ops/gate745-fixture-proof?selector=all"
        },
    ],
)
def test_production_host_database_and_selectors_denied(changes: dict[str, str]) -> None:
    value = manifest()
    value["binding"]["target"].update(changes)
    with pytest.raises(FixtureDenied):
        admit_manifest(raw_manifest(value), now=NOW)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("authority", "ownerId", 316468440),
        ("authority", "workflowPath", ".github/workflows/gate-snapshot-cutover.yml"),
        ("authority", "protectedEnvironment", "production"),
        ("authority", "expiresAt", "2026-10-11T16:00:00.001Z"),
        ("seed", "expectedUnknown", True),
        ("seed", "baselineRevision", "head"),
        ("source", "reviewBodyDigest", "ACCEPT"),
        ("caps", "helperDeploys", 3),
    ],
)
def test_authority_schema_and_caps_fail_closed(section: str, key: str, value: object) -> None:
    data = manifest()
    data["binding"][section][key] = value
    with pytest.raises(FixtureDenied):
        admit_manifest(raw_manifest(data), now=NOW)


def test_extra_sql_dataset_manifest_keys_cannot_select_operations() -> None:
    for key in ("sql", "dsn", "dataset", "command", "mode", "ACCEPT"):
        data = manifest()
        data["binding"][key] = "untrusted"
        with pytest.raises(FixtureDenied, match="E_FIXTURE_SHAPE"):
            admit_manifest(raw_manifest(data), now=NOW)


@pytest.mark.parametrize("key", ["projectId", "environmentId", "serviceId", "currentSourceSha"])
def test_runtime_mismatch_stops_before_executor(key: str) -> None:
    binding = admit_manifest(raw_manifest(), now=NOW)
    controls = {
        "RAILWAY_PROJECT_ID": binding.target["projectId"],
        "RAILWAY_ENVIRONMENT_ID": binding.target["environmentId"],
        "RAILWAY_SERVICE_ID": binding.target["serviceId"],
        "RAILWAY_GIT_COMMIT_SHA": "c" * 40,
    }
    from app.worker.gate745_fixture_policy import RUNTIME_KEYS

    controls[RUNTIME_KEYS[key]] = "wrong"
    with pytest.raises(FixtureDenied, match="E_FIXTURE_RUNTIME"):
        check_runtime(binding, controls.get)


def test_bound_shape_cannot_invent_trusted_executor() -> None:
    b = admit_manifest(raw_manifest(), now=NOW)
    for fake in (None, True, {"accepted": True}, lambda: proof()):
        with pytest.raises(FixtureDenied, match="E_FIXTURE_TRUSTED_ROUTE_MISSING"):
            asyncio.run(authorize("unknown-proof", b, fake, now=NOW))


@pytest.mark.parametrize(
    "changes",
    [
        {"source_sha": "e" * 40},
        {"source_tree": "f" * 40},
        {"binding_digest": HASH},
        {"owner_grant_comment_id": "1003"},
        {"run_attempt": 2},
        {"invocation": 5},
        {"total_invocations": 8},
        {"protected_environment": "production"},
    ],
)
def test_authenticated_proof_still_requires_exact_source_grant_and_budget(
    changes: dict[str, Any],
) -> None:
    b = admit_manifest(raw_manifest(), now=NOW)
    with pytest.raises(FixtureDenied):
        check_execution(b, replace(proof(), **changes), "unknown-proof", now=NOW)


def test_reset_requires_distinct_fresh_grant_quiescence_and_retained_case() -> None:
    b = admit_manifest(raw_manifest(), now=NOW)
    reset = replace(
        proof("reset"),
        quiescence_receipt_digest=HASH,
        retained_case_receipt_digest=HASH,
        reset_grant_comment_id="1003",
        reset_grant_body_digest=HASH,
    )
    check_execution(b, reset, "reset", now=NOW)
    for field in (
        "quiescence_receipt_digest",
        "retained_case_receipt_digest",
        "reset_grant_comment_id",
        "reset_grant_body_digest",
    ):
        with pytest.raises(FixtureDenied):
            check_execution(b, replace(reset, **{field: None}), "reset", now=NOW)
    with pytest.raises(FixtureDenied, match="E_FIXTURE_FRESH_RESET_GRANT"):
        check_execution(b, replace(reset, reset_grant_comment_id="1002"), "reset", now=NOW)


def test_t100_stops_new_setup_t120_stops_readonly_proof() -> None:
    b = admit_manifest(raw_manifest(), now=NOW)
    with pytest.raises(FixtureDenied, match="E_FIXTURE_NEW_TEST_DEADLINE"):
        check_execution(b, proof("bootstrap"), "bootstrap", now=NOW + timedelta(minutes=100))
    check_execution(b, proof(), "unknown-proof", now=NOW + timedelta(minutes=100))
    with pytest.raises(FixtureDenied, match="E_FIXTURE_EXPIRED"):
        check_execution(b, proof(), "unknown-proof", now=NOW + timedelta(minutes=120))


def test_literal_recipe_has_no_active_accounts_provider_ids_or_tokens() -> None:
    assert ROWS["bots"]["token_secret_ref"] is None
    assert ROWS["bots"]["is_active"] is False
    assert ROWS["accounts"]["read_enabled"] is False
    assert ROWS["accounts"]["send_enabled"] is False
    assert ROWS["accounts"]["is_active"] is False
    assert ROWS["send_attempts"]["provider_message_id"] is None
    assert ROWS["send_attempts"]["status"] == "unknown"
    assert JOURNAL_DIGEST == digest(canonical([ROWS["send_attempts"]]))
    changed = copy.deepcopy(ROWS["send_attempts"])
    changed["status"] = "sending"
    assert digest(canonical([changed])) != JOURNAL_DIGEST


def test_installed_unbound_manifest_cannot_be_bypassed_by_direct_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PoisonExecutor(TrustedFixtureExecutor):
        async def authenticate(self, mode: Any, binding: Any) -> ExecutionProof:
            raise AssertionError("must not authenticate")

        def database_url(self) -> str:
            raise AssertionError("must not read DB credential")

        def fixture_admin_token(self) -> str:
            raise AssertionError("must not read admin credential")

    with pytest.raises(FixtureDenied, match="E_FIXTURE_UNBOUND"):
        asyncio.run(
            helper._execute_admitted(
                admit_manifest(raw_manifest(), now=NOW), PoisonExecutor(), proof()
            )
        )
    assert json.loads(MANIFEST_JSON)["binding"] is None


@pytest.mark.parametrize("flag", ["--dsn", "--sql", "--dataset", "--command", "--manifest"])
def test_cli_rejects_arbitrary_operation_inputs_before_env(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setattr(helper, "os", SimpleNamespace(environ=ExplodingEnvironment()))
    monkeypatch.setattr(sys, "argv", ["fixture-helper", "bootstrap", flag, "untrusted"])
    with pytest.raises(SystemExit) as raised:
        helper.main()
    assert raised.value.code == 2


def test_actual_gate_schema_import_inventory_is_data_free() -> None:
    names = helper._known_tables()
    assert set(ROWS) <= names
    assert "alembic_version" not in names
    assert "reply_dialog_versions" not in names


def test_internal_physical_production_target_denies_before_query() -> None:
    class PoisonConnection:
        def execute(self, *args: object) -> object:
            raise AssertionError("must not query Production even via private backend")

    b = admit_manifest(raw_manifest(), now=NOW)
    bad = replace(b, target={**b.target, "serviceId": sorted(PRODUCTION_IDS)[0]})
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PRODUCTION"):
        helper._identity(PoisonConnection(), bad)


def test_actual_async_deadline_cancels_before_db_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlalchemy.ext.asyncio as sqlalchemy_async

    value = manifest()
    value["binding"]["authority"]["expiresAt"] = "2026-10-11T14:00:00.050Z"
    raw = raw_manifest(value)
    binding = admit_manifest(raw, now=NOW)
    evidence = replace(proof(), binding_digest=binding.manifest_digest)
    monkeypatch.setattr(policy, "MANIFEST_JSON", raw)
    monkeypatch.setattr(helper, "datetime", SimpleNamespace(now=lambda tz: NOW))
    t = binding.target
    monkeypatch.setattr(
        helper,
        "os",
        SimpleNamespace(
            environ={
                "RAILWAY_PROJECT_ID": t["projectId"],
                "RAILWAY_ENVIRONMENT_ID": t["environmentId"],
                "RAILWAY_SERVICE_ID": t["serviceId"],
                "RAILWAY_GIT_COMMIT_SHA": "c" * 40,
            }
        ),
    )
    disposed: list[bool] = []

    class WaitingBegin:
        async def __aenter__(self) -> object:
            await asyncio.Event().wait()
            raise AssertionError("deadline must cancel before a connection exists")

        async def __aexit__(self, *args: object) -> None:
            pass

    class WaitingEngine:
        def begin(self) -> WaitingBegin:
            return WaitingBegin()

        async def dispose(self) -> None:
            disposed.append(True)

    def factory(url: Any, **kwargs: Any) -> WaitingEngine:
        assert kwargs["connect_args"] == {
            "timeout": 5,
            "server_settings": {
                "statement_timeout": "15000",
                "lock_timeout": "2000",
            },
        }
        return WaitingEngine()

    class SyntheticExecutor(TrustedFixtureExecutor):
        async def authenticate(self, mode: Any, binding: Any) -> ExecutionProof:
            return evidence

        def database_url(self) -> str:
            return (
                "postgresql+asyncpg://gate745_synthetic:synthetic-only@"
                "gate745-synthetic-postgres.railway.internal:5432/gate745_synthetic"
            )

        def fixture_admin_token(self) -> str:
            raise AssertionError("backend must not read admin token")

    monkeypatch.setattr(sqlalchemy_async, "create_async_engine", factory)
    with pytest.raises(TimeoutError):
        asyncio.run(helper._execute_admitted(binding, SyntheticExecutor(), evidence))
    assert disposed == [True]


@pytest.mark.parametrize(
    "kind,nullable,accepted",
    [("double", True, True), ("real", True, False), ("double", False, False),
     ("integer", True, False)],
)
def test_fixture_schema_uses_exact_postgres_float_storage_and_nullability(
    monkeypatch: pytest.MonkeyPatch, kind: str, nullable: bool, accepted: bool,
) -> None:
    import sqlalchemy
    from app.models.domain import Base
    from sqlalchemy import Column, Float, Integer, MetaData, String, Table
    from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, REAL, dialect

    expected = Table(
        "gate745_type_probe", Base.metadata,
        Column("id", Integer(), primary_key=True),
        Column("confidence", Float(), nullable=True),
    )

    def reflect(metadata: MetaData, **kwargs: Any) -> None:
        observed = expected.to_metadata(metadata, schema="public")
        observed.c.confidence.type = {
            "double": DOUBLE_PRECISION(), "real": REAL(), "integer": Integer(),
        }[kind]
        observed.c.confidence.nullable = nullable
        Table(
            "alembic_version", metadata,
            Column("version_num", String(32), primary_key=True), schema="public",
        )

    inspector = SimpleNamespace(
        get_table_names=lambda **kwargs: ["gate745_type_probe", "alembic_version"],
        get_view_names=lambda **kwargs: [],
        get_materialized_view_names=lambda **kwargs: [],
        get_foreign_table_names=lambda **kwargs: [],
    )

    def no_public_functions(statement: Any) -> Any:
        assert "FROM pg_proc p" in str(statement)
        return SimpleNamespace(scalar_one=lambda: 0)

    connection = SimpleNamespace(dialect=dialect(), execute=no_public_functions)
    monkeypatch.setattr(helper, "_known_tables", lambda: {"gate745_type_probe"})
    monkeypatch.setattr(sqlalchemy, "inspect", lambda connection: inspector)
    monkeypatch.setattr(MetaData, "reflect", reflect)
    try:
        if accepted:
            actual = helper._tables(connection, BASELINE)
            assert str(actual.tables["public.gate745_type_probe"].c.confidence.type) == (
                "DOUBLE PRECISION"
            )
        else:
            with pytest.raises(FixtureDenied, match="E_FIXTURE_SCHEMA"):
                helper._tables(connection, BASELINE)
    finally:
        Base.metadata.remove(expected)


def test_reviewed_migration_bundle_survives_installed_wheel_and_docker_layout(
    tmp_path: Any,
) -> None:
    """Build/install the actual wheel away from cwd; reconstruct Docker COPY artifact."""
    import hashlib
    import importlib.util
    import os
    import shutil
    import subprocess
    import venv
    import zipfile
    from pathlib import Path

    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    environment = {"PATH": os.environ.get("PATH", ""), "PIP_NO_INDEX": "1"}
    if "SystemRoot" in os.environ:
        environment["SystemRoot"] = os.environ["SystemRoot"]
    environment["PYTHONPATH"] = os.pathsep.join(
        str(Path(p).resolve())
        for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if p and Path(p).resolve() != root
    )

    def checked(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # These fixed offline commands inherit only the allowlisted
        # environment above; no credential/provider environment is passed.
        if result.returncode != 0:
            pytest.fail(
                f"Offline fixture subprocess exit {result.returncode}: {result.stderr[-3000:]}",
                pytrace=False,
            )
        return result

    checked(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(wheels),
            str(root),
        ],
        timeout=90,
    )
    (wheel,) = wheels.glob("gate-*.whl")
    # Represent the same-version distribution inherited in hosted CI, even
    # when this local parent has no Gate installation. It contains metadata
    # only, so it cannot supply the executing helper if wheel install skips.
    inherited = tmp_path / "inherited"
    with zipfile.ZipFile(wheel) as archive:
        (metadata_name,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        metadata_path = inherited / metadata_name
        metadata_path.parent.mkdir(parents=True)
        metadata_path.write_bytes(archive.read(metadata_name))
    environment["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(inherited), environment["PYTHONPATH"]) if p
    )
    installed = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(installed)
    python = installed / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    checked(
        [str(python), "-m", "pip", "install", "--ignore-installed", "--no-deps", "--no-index",
         str(wheel)],
        timeout=60,
    )
    probe = """
import importlib, json, shutil, sys
from importlib.metadata import distribution
from pathlib import Path
from app.worker import gate745_fixture_bootstrap as h
from app.worker.gate745_fixture_policy import FixtureDenied
from alembic.script import ScriptDirectory
executing = Path(h.__file__).resolve()
assert executing.is_relative_to(Path(sys.prefix).resolve())
versions = h._migration_versions()
assert versions == Path(sys.prefix) / 'share/gate745-fixture/alembic/versions'
script = ScriptDirectory.from_config(h._migration_config())
assert script.get_revision(h.BASELINE).revision == h.BASELINE
assert len(list(script.walk_revisions(base='base', head=h.BASELINE))) == 119
# Load the actual installed module first, then expose real checkout egg-info.
# Unrestricted distribution('gate') now chooses the wrong source artifact.
sys.path.insert(0, sys.argv[1])
assert Path(distribution('gate').locate_file(
    'app/worker/gate745_fixture_bootstrap.py'
)).resolve() != executing
assert h._migration_versions() == versions
sys.path.pop(0)
installation = executing.parents[2]

def denied():
    try:
        h._migration_versions()
    except FixtureDenied as exc:
        assert str(exc) == 'E_FIXTURE_MIGRATION_ARTIFACT'
    else:
        raise AssertionError('Unproven distribution metadata must deny')

duplicate = installation / 'gate-9.9.9.dist-info'
duplicate.mkdir()
(duplicate / 'METADATA').write_text('Metadata-Version: 2.1\\nName: gate\\nVersion: 9.9.9\\n')
try:
    importlib.invalidate_caches()
    denied()
finally:
    shutil.rmtree(duplicate)
(metadata,) = installation.glob('gate-*.dist-info')
hidden = Path(sys.prefix) / 'withheld-gate-metadata'
metadata.rename(hidden)
try:
    importlib.invalidate_caches()
    denied()
finally:
    hidden.rename(metadata)
importlib.invalidate_caches()
assert h._migration_versions() == versions
print(json.dumps({'files': len(h.MIGRATION_SHA256), 'baseline': h.BASELINE,
                 'checkoutShadow': 'PASS', 'missingMetadata': 'DENIED',
                 'ambiguousMetadata': 'DENIED'}))
"""
    actual = checked(
        [str(python), "-c", probe, str(root)],
        timeout=30,
    )
    assert json.loads(actual.stdout) == {
        "files": 119,
        "baseline": BASELINE,
        "checkoutShadow": "PASS",
        "missingMetadata": "DENIED",
        "ambiguousMetadata": "DENIED",
    }
    # Actual existing Dockerfile copies these canonical paths to /app. No
    # Docker daemon/network/container privilege is needed to test discovery.
    dockerfile = (root / "Dockerfile").read_text()
    assert "WORKDIR /app" in dockerfile
    assert "COPY app ./app" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY pyproject.toml alembic.ini ./" in dockerfile
    artifact = tmp_path / "docker" / "app"
    worker = artifact / "app" / "worker"
    worker.mkdir(parents=True)
    shutil.copy(root / "pyproject.toml", artifact)
    shutil.copy(root / "app/worker/gate745_fixture_bootstrap.py", worker)
    shutil.copytree(root / "app/worker/gate745_fixture_alembic", worker / "gate745_fixture_alembic")
    shutil.copytree(root / "alembic/versions", artifact / "alembic/versions")
    spec = importlib.util.spec_from_file_location(
        "fixture_docker_artifact", worker / "gate745_fixture_bootstrap.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._migration_versions() == artifact / "alembic/versions"
    script = ScriptDirectory.from_config(module._migration_config())
    assert script.get_revision(BASELINE).revision == BASELINE
    for name, expected in helper.MIGRATION_SHA256.items():
        assert (
            hashlib.sha256((artifact / "alembic/versions" / name).read_bytes()).hexdigest()
            == expected
        )
    # Missing/substituted/extended artifacts DENY before any migration/DB work.
    baseline = artifact / "alembic/versions/0115_watch_voice_post_payment.py"
    original = baseline.read_bytes()
    baseline.write_bytes(original + b"\n# substituted artifact\n")
    with pytest.raises(FixtureDenied, match="E_FIXTURE_MIGRATION_ARTIFACT"):
        module._migration_config()
    baseline.write_bytes(original)
    extra = artifact / "alembic/versions/unreviewed.py"
    extra.write_text("revision='unreviewed'\n")
    with pytest.raises(FixtureDenied, match="E_FIXTURE_MIGRATION_ARTIFACT"):
        module._migration_config()
    extra.unlink()
    baseline.unlink()
    with pytest.raises(FixtureDenied, match="E_FIXTURE_MIGRATION_ARTIFACT"):
        module._migration_config()
