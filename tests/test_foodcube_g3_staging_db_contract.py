"""FoodCube G3 isolated staging database contract regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from app.infrastructure.foodcube_g3_staging_db import (
    EXPECTED_DATABASE,
    EXPECTED_SCHEMA,
    MIGRATION_ROLE,
    POSTGRES_MAJOR,
    RUNTIME_ROLE,
    resolve_foodcube_g3_staging_database,
    role_contract_sql,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "registry" / "foodcube-g3-staging-db-contract.v1.json"


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        "GATE_FOODCUBE_G3_ENV": "staging",
        "GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL": (
            "postgresql+asyncpg://gate_g3_runtime:synthetic-runtime"
            "@localhost:5432/gate_g3_staging"
        ),
        "GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL": (
            "postgresql+asyncpg://gate_g3_migrator:synthetic-migration"
            "@localhost:5432/gate_g3_staging"
        ),
    }
    environment.update(overrides)
    return environment


def test_contract_identity_and_restore_receipt_are_complete() -> None:
    payload: dict[str, Any] = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert payload["state"] == "repository_contract_only_default_off"
    assert payload["postgres"] == {
        "major": 17,
        "image_policy": "postgres:17",
        "pitr_policy": "major-tag",
    }
    assert payload["identity"] == {
        "environment": "staging",
        "database": EXPECTED_DATABASE,
        "schema": EXPECTED_SCHEMA,
        "migration_role": MIGRATION_ROLE,
        "runtime_role": RUNTIME_ROLE,
    }
    required = set(payload["restore_smoke_receipt"]["required"])
    assert {
        "candidate_sha",
        "backup_sha256",
        "migration_head",
        "row_count_fingerprint",
        "smoke_passed",
        "runtime_ddl_denied",
        "food_database_denied",
        "production_fallback_denied",
        "secret_values_present",
        "provider_effects",
    } <= required
    assert payload["restore_smoke_receipt"]["invariants"] == {
        "secret_values_present": False,
        "provider_effects": 0,
        "runtime_ddl_denied": True,
        "food_database_denied": True,
        "production_fallback_denied": True,
    }


def test_resolver_returns_only_redacted_identity() -> None:
    identity = resolve_foodcube_g3_staging_database(_environment())
    assert identity.receipt_fields() == {
        "environment": "staging",
        "database": EXPECTED_DATABASE,
        "schema": EXPECTED_SCHEMA,
        "migration_role": MIGRATION_ROLE,
        "runtime_role": RUNTIME_ROLE,
        "postgres_major": POSTGRES_MAJOR,
    }
    wire = json.dumps(identity.receipt_fields())
    assert "synthetic-runtime" not in wire
    assert "synthetic-migration" not in wire


@pytest.mark.parametrize("identity", ["", "development", "production", "preview", "unknown"])
def test_missing_unknown_and_production_environment_fail_closed(identity: str) -> None:
    with pytest.raises(RuntimeError, match="must be exactly staging"):
        resolve_foodcube_g3_staging_database(
            _environment(GATE_FOODCUBE_G3_ENV=identity)
        )


def test_generic_production_database_url_is_never_a_fallback() -> None:
    with pytest.raises(RuntimeError, match="both isolated G3 database URLs"):
        resolve_foodcube_g3_staging_database(
            {
                "GATE_FOODCUBE_G3_ENV": "staging",
                "DATABASE_URL": "postgresql://postgres:production@prod/gate",
            }
        )


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        (
            "GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL",
            "postgresql://gate_g3_runtime:synthetic@localhost/food_staging",
            "must target gate_g3_staging",
        ),
        (
            "GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL",
            "postgresql://postgres:synthetic@localhost/gate_g3_staging",
            "must not use a shared superuser",
        ),
        (
            "GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL",
            "postgresql://gate_g3_migrator:synthetic@localhost/gate_g3_staging",
            "must use role gate_g3_runtime",
        ),
        (
            "GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL",
            "postgresql://gate_g3_runtime:synthetic@localhost/gate_g3_staging",
            "must use role gate_g3_migrator",
        ),
    ],
)
def test_food_database_shared_superuser_and_cross_role_are_denied(
    variable: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        resolve_foodcube_g3_staging_database(_environment(**{variable: value}))


def test_shared_role_password_is_denied() -> None:
    with pytest.raises(RuntimeError, match="must not share credentials"):
        resolve_foodcube_g3_staging_database(
            _environment(
                GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL=(
                    "postgresql://gate_g3_runtime:same@localhost/gate_g3_staging"
                ),
                GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL=(
                    "postgresql://gate_g3_migrator:same@localhost/gate_g3_staging"
                ),
            )
        )


def test_runtime_sql_contract_contains_no_ddl_grant_or_food_access() -> None:
    sql = "\n".join(role_contract_sql()).casefold()
    assert f"grant usage on schema {EXPECTED_SCHEMA}" in sql
    assert "grant select, insert, update, delete on tables" in sql
    assert "grant create" not in sql
    assert "grant alter" not in sql
    assert "grant drop" not in sql
    assert "food" not in sql
    assert "superuser" not in sql


@pytest.mark.anyio
async def test_disposable_postgres_enforces_runtime_ddl_and_food_db_denial() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("disposable PostgreSQL is not configured")

    import asyncpg

    admin_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    admin = await asyncpg.connect(admin_url)
    runtime_role = "gate_g3_runtime_contract_test"
    migration_role = "gate_g3_migrator_contract_test"
    schema = "gate_g3_contract_test"
    food_database = "food_g3_forbidden_contract_test"
    try:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime_role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{migration_role}"')
        await admin.execute(
            f"""CREATE ROLE "{migration_role}" LOGIN PASSWORD 'synthetic-migration'
            NOSUPERUSER NOCREATEDB NOCREATEROLE"""
        )
        await admin.execute(
            f"""CREATE ROLE "{runtime_role}" LOGIN PASSWORD 'synthetic-runtime'
            NOSUPERUSER NOCREATEDB NOCREATEROLE"""
        )
        await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{migration_role}"')
        await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime_role}"')
        await admin.execute(
            f'SET ROLE "{migration_role}"; CREATE TABLE "{schema}".owned(id bigint); RESET ROLE'
        )
        await admin.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{schema}".owned TO "{runtime_role}"'
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{food_database}"')
        await admin.execute(f'CREATE DATABASE "{food_database}"')
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{food_database}" FROM PUBLIC')

        runtime = await asyncpg.connect(
            host="localhost",
            port=5432,
            user=runtime_role,
            password="synthetic-runtime",
            database="gate",
        )
        try:
            await runtime.execute(f'INSERT INTO "{schema}".owned(id) VALUES (1)')
            for statement in (
                f'CREATE TABLE "{schema}".forbidden(id bigint)',
                f'ALTER TABLE "{schema}".owned ADD COLUMN forbidden text',
                f'DROP TABLE "{schema}".owned',
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(statement)
        finally:
            await runtime.close()

        with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
            await asyncpg.connect(
                host="localhost",
                port=5432,
                user=runtime_role,
                password="synthetic-runtime",
                database=food_database,
            )
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{food_database}"')
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime_role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{migration_role}"')
        await admin.close()
