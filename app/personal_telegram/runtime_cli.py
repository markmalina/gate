"""Operator entry point for the isolated, non-sending MTProto RU connection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.infrastructure.database import database_runtime_lifespan
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.cloud_session import (
    CloudEncryptedSessionSource,
    CloudSessionBinding,
)
from app.personal_telegram.cloud_worker import (
    RuCloudWorker,
    TelegramApplication,
    TelethonRuntimeClient,
)
from app.personal_telegram.command_scope import CommandRuntimeError, RuCommandScope, utc_now
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.session_persistence import SessionCipher


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
    scope, cipher = config.scope(), config.cipher()
    value = CloudEncryptedSessionSource(legacy).load(session_ref=legacy.session_ref)
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
            cipher.unseal(scope, ciphertext=row.ciphertext, generation=row.session_generation)
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


async def run(
    action: Literal["inspect", "import-existing", "run"],
    seconds: int,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    if action == "run" and environment.get("GATE_PERSONAL_RUNTIME_ENABLED", "").lower() != "true":
        return {"account": "RU", "state": "disabled"}
    config = RuntimeSettings()  # type: ignore[call-arg]
    dsn = config.database_url.get_secret_value()
    if not dsn.startswith("postgresql+asyncpg://"):
        raise CommandRuntimeError("personal_runtime_database_required")
    if not 20 <= seconds <= 28_800:
        raise CommandRuntimeError("runtime_window_invalid")
    async with database_runtime_lifespan(dsn) as database:
        sessions = database.session_factory
        if action == "inspect":
            return await inspect_runtime(sessions, config)
        if action == "import-existing":
            return await import_existing(sessions, config, environment)
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
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        timer = loop.call_later(seconds, stop.set)
        installed = []
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with suppress(NotImplementedError):
                    loop.add_signal_handler(sig, stop.set)
                    installed.append(sig)
            outcome = await worker.run_until(stop)
        finally:
            timer.cancel()
            for sig in installed:
                loop.remove_signal_handler(sig)
        return {"account": "RU", "state": outcome.state, "error_code": outcome.code}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "import-existing", "run"))
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        outcome = asyncio.run(run(args.action, args.seconds, os.environ))
    except (CommandRuntimeError, PersonalTelegramTransportError) as exc:
        outcome = {"account": "RU", "state": "failed", "error_code": str(exc)}
    except (SQLAlchemyError, ValidationError, OSError, ValueError):
        outcome = {"account": "RU", "state": "failed", "error_code": "runtime_configuration_failed"}
    print(json.dumps(outcome, sort_keys=True))
    if outcome.get("state") in {"failed", "blocked"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
