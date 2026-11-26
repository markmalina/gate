"""Provision the isolated S1 role and database on the existing staging Postgres."""

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets

import asyncpg

ROLE = "gate696_s1_owner"
DATABASE = "gate696_s1_journal"


def scram_verifier(password: str) -> str:
    def encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    stored_key = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    return f"SCRAM-SHA-256$4096:{encode(salt)}${encode(stored_key)}:{encode(server_key)}"


async def provision() -> None:
    if (
        os.environ.get("GATE_ENV") != "staging"
        or os.environ.get("RAILWAY_ENVIRONMENT_ID") != "3c1412a1-58c6-49c2-ad4f-1f50c3c9946f"
        or os.environ.get("RAILWAY_SERVICE_ID") != "3d41ea2b-9439-42e4-8399-de85da05ad9d"
    ):
        raise RuntimeError("unexpected_stage")
    password = os.environ.get("GATE_S1_DB_ROLE_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", password):
        raise RuntimeError("invalid_stage_password")
    admin = await asyncpg.connect(os.environ["GATE_S1_BOOTSTRAP_ADMIN_DSN"], ssl="require")
    try:
        async with admin.transaction():
            await admin.execute("SELECT pg_advisory_xact_lock(696101)")
            existing = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
            database = await admin.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", DATABASE
            )
            if existing or database:
                raise RuntimeError("stage_role_or_database_already_exists")
            await admin.execute(
                f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"NOREPLICATION NOBYPASSRLS PASSWORD '{scram_verifier(password)}'"
            )
        await admin.execute(f"CREATE DATABASE {DATABASE} OWNER {ROLE} TEMPLATE template0")
        await admin.execute(f"REVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC")
        await admin.execute(f"GRANT CONNECT ON DATABASE {DATABASE} TO {ROLE}")
        identity = await admin.fetchrow(
            "SELECT pg_get_userbyid(datdba) AS owner, version() AS version "
            "FROM pg_database WHERE datname = $1",
            DATABASE,
        )
        if identity is None or identity["owner"] != ROLE:
            raise RuntimeError("stage_database_owner_mismatch")
        print("gate696_s1_database_ready", identity["version"].split()[1])
    finally:
        await admin.close()


if __name__ == "__main__":
    try:
        asyncio.run(provision())
    except Exception as error:
        raise SystemExit(f"gate696_s1_database_failed {type(error).__name__}") from None
