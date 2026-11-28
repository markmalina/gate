"""Operator entry point for the isolated, non-sending MTProto RU connection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import sys
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.infrastructure.database import database_runtime_lifespan
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.cloud_session import (
    CloudEncryptedSessionSource,
    CloudSessionBinding,
    StoredCloudSession,
)
from app.personal_telegram.cloud_worker import (
    RuCloudWorker,
    TelegramApplication,
    TelethonRuntimeClient,
    WorkerExit,
)
from app.personal_telegram.command_scope import CommandRuntimeError, RuCommandScope, utc_now
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.s2_session_vault import SESSION_REF, open_runtime_s2_session
from app.personal_telegram.session_persistence import SessionCipher

S2_DB_HOST = "gate-ru-store.railway.internal"
S2_DB_NAME = "gate_s2_ru"
S2_MIGRATION = "0125_personal_runtime_check"
S2_SCHEMA_LOCK = 6960125


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATE_PERSONAL_RUNTIME_", hide_input_in_errors=True
    )

    enabled: bool = False
    database_url: SecretStr
    workspace: str
    owner: str
    binding_id: UUID
    session_key: SecretStr
    command_key: SecretStr
    key_version: str = "ru-v1"
    expected_provider_owner: int = Field(gt=0, lt=2**63)
    api_id: int = Field(validation_alias="GATE_PERSONAL_TELEGRAM_API_ID")
    api_hash: SecretStr = Field(validation_alias="GATE_PERSONAL_TELEGRAM_API_HASH")

    def scope(self) -> RuCommandScope:
        return RuCommandScope(
            self.workspace, TELEGRAM_MARK_RU_ACCOUNT_ID, self.binding_id, self.owner
        )

    def cipher(self) -> SessionCipher:
        return SessionCipher(
            key=self.session_key.get_secret_value().encode(), key_version=self.key_version
        )


def _matches_scope(row: RuntimeBindingRow | None, scope: RuCommandScope) -> bool:
    return (
        row is not None
        and row.id == scope.binding_id
        and row.account_id == str(scope.account_id)
        and row.workspace == scope.workspace
        and row.owner_subject == scope.owner_subject
    )


async def import_existing(
    sessions: async_sessionmaker[AsyncSession],
    config: RuntimeSettings,
    environment: Mapping[str, str],
) -> dict[str, object]:
    legacy = CloudSessionBinding.from_environment(environment)
    if (
        legacy.expected_owner_user_id != config.expected_provider_owner
        or legacy.api_id != config.api_id
        or legacy.api_hash != config.api_hash.get_secret_value()
    ):
        raise CommandRuntimeError("ru_session_identity_mismatch")
    value = CloudEncryptedSessionSource(legacy).load(session_ref=legacy.session_ref)
    return await _prepare_session(sessions, config, value, require_identical=False)


async def import_s2_volume(
    sessions: async_sessionmaker[AsyncSession],
    config: RuntimeSettings,
    snapshot: StoredCloudSession,
) -> dict[str, object]:
    if (
        snapshot.api_id != config.api_id
        or snapshot.owner_user_id != config.expected_provider_owner
        or snapshot.session_ref != SESSION_REF
    ):
        raise CommandRuntimeError("ru_session_identity_mismatch")
    return await _prepare_session(sessions, config, snapshot.session, require_identical=True)


async def _prepare_session(
    sessions: async_sessionmaker[AsyncSession],
    config: RuntimeSettings,
    value: str,
    *,
    require_identical: bool,
) -> dict[str, object]:
    scope, cipher = config.scope(), config.cipher()
    scope.validate()
    try:
        async with sessions.begin() as session:
            session.add(
                RuntimeBindingRow(
                    id=scope.binding_id,
                    workspace=scope.workspace,
                    account_id=str(scope.account_id),
                    owner_subject=scope.owner_subject,
                    session_generation=1,
                    revision=0,
                    ciphertext=cipher.seal(scope, session=value, generation=1),
                    key_version=cipher.key_version,
                    custody_state="prepared",
                    health_state="disconnected",
                )
            )
    except IntegrityError:
        async with sessions() as session:
            row = await session.get(RuntimeBindingRow, scope.binding_id)
            if row is None or not _matches_scope(row, scope) or row.custody_state != "prepared":
                raise CommandRuntimeError("ru_binding_conflict") from None
            if row.key_version != cipher.key_version:
                raise CommandRuntimeError("session_key_version_invalid") from None
            existing = cipher.unseal(
                scope,
                ciphertext=row.ciphertext,
                generation=row.session_generation,
            )
            if require_identical and not secrets.compare_digest(existing.encode(), value.encode()):
                raise CommandRuntimeError("ru_binding_session_conflict") from None
        return {"account": "RU", "state": "already_prepared", "authenticated": None}
    return {"account": "RU", "state": "prepared", "authenticated": None}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def inspect_runtime(
    sessions: async_sessionmaker[AsyncSession], config: RuntimeSettings
) -> dict[str, object]:
    scope = config.scope()
    scope.validate()
    async with sessions() as session:
        row = await session.get(RuntimeBindingRow, scope.binding_id)
        if row is None or not _matches_scope(row, scope):
            return {
                "account": "RU",
                "configured": False,
                "runtime_connected": False,
                "error_code": "ru_binding_not_found",
            }
        verified, expires, now = row.last_verified_at, row.lease_expires_at, utc_now()
        configured = False
        error = None
        if row.custody_state == "prepared":
            cipher = config.cipher()
            try:
                if row.key_version != cipher.key_version:
                    raise CommandRuntimeError("session_key_version_invalid")
                value = cipher.unseal(
                    scope, ciphertext=row.ciphertext, generation=row.session_generation
                )
                if not value:
                    raise CommandRuntimeError("session_envelope_unavailable")
                configured = True
            except CommandRuntimeError as exc:
                error = str(exc)
        connected = bool(
            configured
            and row.health_state == "ready"
            and row.lease_owner is not None
            and row.last_verified_generation == row.lease_generation
            and verified is not None
            and _utc(verified) <= now
            and expires is not None
            and _utc(expires) > now
        )
        if not connected and error is None:
            if row.lease_owner is not None and (expires is None or _utc(expires) <= now):
                error = "runtime_lease_expired"
            else:
                error = "runtime_not_ready"
        return {
            "account": "RU",
            "configured": configured,
            "authenticated": True if connected else None,
            "custody_state": row.custody_state,
            "runtime_connected": connected,
            "owner_lease_present": row.lease_owner is not None,
            "lease_generation": row.lease_generation,
            "last_successful_check": _utc(verified).isoformat() if verified is not None else None,
            "state": row.health_state,
            "send_enabled": False,
            "create_group_enabled": False,
            "error_code": error,
        }


def require_s2_database(dsn: str) -> None:
    url = make_url(dsn)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != S2_DB_HOST
        or url.port not in (None, 5432)
        or url.database != S2_DB_NAME
        or url.username != "gate_s2"
        or bool(url.query)
    ):
        raise CommandRuntimeError("s2_runtime_database_scope_mismatch")


async def prepare_s2_schema(engine: AsyncEngine, dsn: str) -> None:
    """Initialize only the private S2 database with the versioned Gate migrations."""
    async with asyncio.timeout(95), engine.connect() as connection:
        version = await connection.exec_driver_sql("SHOW server_version_num")
        if int(version.scalar_one()) // 10000 != 17:
            raise CommandRuntimeError("s2_runtime_postgres_version_mismatch")
        await connection.exec_driver_sql(f"SELECT pg_advisory_lock({S2_SCHEMA_LOCK})")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                S2_MIGRATION,
                env={"DATABASE_URL": dsn},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                if await process.wait() != 0:
                    raise CommandRuntimeError("s2_runtime_schema_upgrade_failed")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        finally:
            await connection.exec_driver_sql(f"SELECT pg_advisory_unlock({S2_SCHEMA_LOCK})")


async def run(
    action: Literal["inspect", "import-existing", "import-volume", "bootstrap-volume", "run"],
    seconds: int,
    environment: Mapping[str, str],
    *,
    s2_owner: bool = False,
) -> dict[str, Any]:
    if action == "run" and environment.get("GATE_PERSONAL_RUNTIME_ENABLED", "").lower() != "true":
        return {"account": "RU", "state": "disabled"}
    from_volume = action in {"import-volume", "bootstrap-volume"} or s2_owner
    source = open_runtime_s2_session(environment) if from_volume else None
    if source is None:
        config = RuntimeSettings()  # type: ignore[call-arg]
    else:
        config = RuntimeSettings(expected_provider_owner=source.owner_user_id)  # type: ignore[call-arg]
    dsn = config.database_url.get_secret_value()
    if not dsn.startswith("postgresql+asyncpg://"):
        raise CommandRuntimeError("personal_runtime_database_required")
    if from_volume:
        require_s2_database(dsn)
    if not 20 <= seconds <= 28_800:
        raise CommandRuntimeError("runtime_window_invalid")
    async with database_runtime_lifespan(dsn) as database:
        sessions = database.session_factory
        if action == "inspect":
            return await inspect_runtime(sessions, config)
        if action == "import-existing":
            return await import_existing(sessions, config, environment)
        if action in {"import-volume", "bootstrap-volume"}:
            assert source is not None
            if action == "bootstrap-volume":
                await prepare_s2_schema(database.engine, dsn)
            result = await import_s2_volume(sessions, config, source)
            status = await inspect_runtime(sessions, config)
            if status["configured"] is not True:
                raise CommandRuntimeError("s2_runtime_binding_readback_failed")
            return {**result, "configured": True, "runtime_connected": status["runtime_connected"]}
        app = TelegramApplication(config.api_id, config.api_hash.get_secret_value())
        app.validate()
        worker = RuCloudWorker(
            store=SqlCommandStore(
                sessions,
                scope=config.scope(),
                payload_cipher=CommandPayloadCipher(
                    config.command_key.get_secret_value().encode(),
                ),
            ),
            cipher=config.cipher(),
            session_generation=1,
            expected_provider_user_id=config.expected_provider_owner,
            client_factory=lambda session: TelethonRuntimeClient(session, app),
            enabled=config.enabled,
        )
        outcome = await run_worker(worker, seconds)
        return {"account": "RU", "state": outcome.state, "error_code": outcome.code}


async def run_worker(worker: RuCloudWorker, seconds: float) -> WorkerExit:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    timer = loop.call_later(seconds, stop.set)
    installed = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
                installed.append(sig)
        return await worker.run_until(stop)
    finally:
        timer.cancel()
        for sig in installed:
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("inspect", "import-existing", "import-volume", "bootstrap-volume", "run")
    )
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument(
        "--s2-owner", action="store_true", help="read the owner pin on the S2 volume"
    )
    args = parser.parse_args()
    if args.s2_owner and args.action not in {"run", "inspect"}:
        parser.error("--s2-owner requires run or inspect")
    try:
        outcome = asyncio.run(run(args.action, args.seconds, os.environ, s2_owner=args.s2_owner))
    except (CommandRuntimeError, PersonalTelegramTransportError) as exc:
        outcome = {"account": "RU", "state": "failed", "error_code": str(exc)}
    except (SQLAlchemyError, ValidationError, OSError, ValueError, TimeoutError):
        outcome = {"account": "RU", "state": "failed", "error_code": "runtime_configuration_failed"}
    print(json.dumps(outcome, sort_keys=True))
    if outcome.get("state") in {"failed", "blocked"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
