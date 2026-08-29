"""Fail-closed FoodCube G3 staging database contract.

This module validates configuration and renders redacted repository evidence only.
It never opens a connection or mutates a provider/database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from urllib.parse import unquote, urlparse

POSTGRES_MAJOR = 17
EXPECTED_ENVIRONMENT = "staging"
EXPECTED_DATABASE = "gate_g3_staging"
EXPECTED_SCHEMA = "gate_g3"
MIGRATION_ROLE = "gate_g3_migrator"
RUNTIME_ROLE = "gate_g3_runtime"
_FORBIDDEN_SUPERUSERS = frozenset({"postgres", "railway", "root", "admin"})


@dataclass(frozen=True, slots=True)
class RedactedDatabaseIdentity:
    """Non-secret identity safe for receipts and diagnostics."""

    environment: str
    database: str
    schema: str
    migration_role: str
    runtime_role: str
    postgres_major: int

    def receipt_fields(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ParsedDsn:
    username: str
    password: str
    hostname: str
    port: int
    database: str


def _parse_dsn(value: str, *, variable: str) -> _ParsedDsn:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError(f"{variable} must use PostgreSQL")
    if not parsed.username or parsed.password is None or not parsed.hostname:
        raise RuntimeError(f"{variable} must contain explicit isolated credentials")
    database = parsed.path.removeprefix("/")
    if not database:
        raise RuntimeError(f"{variable} must name an isolated database")
    return _ParsedDsn(
        username=unquote(parsed.username),
        password=unquote(parsed.password),
        hostname=parsed.hostname,
        port=parsed.port or 5432,
        database=database,
    )


def _validate_role(dsn: _ParsedDsn, *, expected: str, variable: str) -> None:
    if dsn.username in _FORBIDDEN_SUPERUSERS:
        raise RuntimeError(f"{variable} must not use a shared superuser")
    if dsn.username != expected:
        raise RuntimeError(f"{variable} must use role {expected}")
    if dsn.database != EXPECTED_DATABASE:
        raise RuntimeError(f"{variable} must target {EXPECTED_DATABASE}")
    if "food" in dsn.database.casefold():
        raise RuntimeError(f"{variable} must not target a Food database")


def resolve_foodcube_g3_staging_database(
    environment: Mapping[str, str],
) -> RedactedDatabaseIdentity:
    """Resolve only the exact G3 staging identity; never fall back to DATABASE_URL."""

    identity = environment.get("GATE_FOODCUBE_G3_ENV", "").strip()
    if identity != EXPECTED_ENVIRONMENT:
        raise RuntimeError("GATE_FOODCUBE_G3_ENV must be exactly staging")

    runtime_value = environment.get("GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL", "")
    migration_value = environment.get("GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL", "")
    if not runtime_value.strip() or not migration_value.strip():
        raise RuntimeError("both isolated G3 database URLs are required")

    runtime = _parse_dsn(
        runtime_value,
        variable="GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL",
    )
    migration = _parse_dsn(
        migration_value,
        variable="GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL",
    )
    _validate_role(
        runtime,
        expected=RUNTIME_ROLE,
        variable="GATE_FOODCUBE_G3_RUNTIME_DATABASE_URL",
    )
    _validate_role(
        migration,
        expected=MIGRATION_ROLE,
        variable="GATE_FOODCUBE_G3_MIGRATION_DATABASE_URL",
    )
    if (runtime.hostname, runtime.port, runtime.database) != (
        migration.hostname,
        migration.port,
        migration.database,
    ):
        raise RuntimeError("migration and runtime URLs must target one isolated Gate database")
    if runtime.password == migration.password:
        raise RuntimeError("migration and runtime roles must not share credentials")

    return RedactedDatabaseIdentity(
        environment=EXPECTED_ENVIRONMENT,
        database=EXPECTED_DATABASE,
        schema=EXPECTED_SCHEMA,
        migration_role=MIGRATION_ROLE,
        runtime_role=RUNTIME_ROLE,
        postgres_major=POSTGRES_MAJOR,
    )


def role_contract_sql() -> tuple[str, ...]:
    """Return provider-neutral SQL applied later by an explicitly authorized operator."""

    return (
        f"REVOKE ALL ON DATABASE {EXPECTED_DATABASE} FROM PUBLIC",
        f"CREATE SCHEMA {EXPECTED_SCHEMA} AUTHORIZATION {MIGRATION_ROLE}",
        f"REVOKE CREATE ON SCHEMA {EXPECTED_SCHEMA} FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA {EXPECTED_SCHEMA} TO {RUNTIME_ROLE}",
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATION_ROLE} "
            f"IN SCHEMA {EXPECTED_SCHEMA} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE}"
        ),
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATION_ROLE} "
            f"IN SCHEMA {EXPECTED_SCHEMA} "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {RUNTIME_ROLE}"
        ),
    )
