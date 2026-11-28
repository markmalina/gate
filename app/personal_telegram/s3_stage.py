"""Explicit staging composition for one owner-approved RU Saved Messages operation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.core.mcp_security import Principal
from app.infrastructure.database import database_runtime_lifespan
from app.infrastructure.personal_telegram_fence import SqlPersonalTelegramSendFence
from app.models.domain import Account
from app.personal_telegram.cloud_worker import (
    RuCloudWorker,
    TelegramApplication,
    TelethonRuntimeClient,
)
from app.personal_telegram.command_scope import CommandRuntimeError, PeerIdentity, utc_now
from app.personal_telegram.command_store import (
    RuntimeBindingRow,
    RuntimeOperationRow,
    _utc,
)
from app.personal_telegram.runtime_cli import RuntimeSettings, require_s2_database, run_worker
from app.personal_telegram.s2_session_vault import open_runtime_s2_session
from app.personal_telegram.saved_messages_dispatch import (
    SavedMessagesApproval,
    SavedMessagesDispatcher,
)
from app.personal_telegram.work_stage import StageJournal, StageSettings

S3_MIGRATION = "0127_personal_s3_operation_permit"


class S3StageSettings(StageSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATE_PERSONAL_S3_STAGE_", hide_input_in_errors=True
    )

    enabled: bool = False
    expected_provider_owner: int = Field(gt=0, lt=2**63)
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")

    @model_validator(mode="after")
    def validate_s3(self) -> S3StageSettings:
        if (
            self.environment != "staging"
            or self.file_host
            or self.s2_status_url
            or urlsplit(self.resource).hostname
            in {
                "gate-mtproto-s1-staging.up.railway.app",
                "gate-ru-enrollment-staging.up.railway.app",
            }
        ):
            raise ValueError("S3 requires the dedicated staging route")
        try:
            require_s2_database(self.database_url.get_secret_value())
        except CommandRuntimeError:
            raise ValueError("S3 requires the isolated RU staging database") from None
        return self

    def key(self, purpose: str) -> bytes:
        return super().key("s3/" + purpose)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise CommandRuntimeError("s3_stage_disabled")

    def require_source(self, environment: Mapping[str, str]) -> None:
        if environment.get("RAILWAY_GIT_COMMIT_SHA") != self.source_revision:
            raise CommandRuntimeError("s3_source_revision_mismatch")


def load_s3_stage() -> S3StageSettings:
    if os.environ.get("GATE_PERSONAL_S3_STAGE_ENABLED") != "true":
        raise CommandRuntimeError("s3_stage_disabled")
    config = S3StageSettings()  # type: ignore[call-arg]
    config.require_source(os.environ)
    return config


class S3StageJournal(StageJournal):
    def __init__(self, sessions: async_sessionmaker[AsyncSession], config: S3StageSettings) -> None:
        config.require_enabled()
        super().__init__(
            sessions, config, peer=PeerIdentity("self", config.expected_provider_owner)
        )
        self.approval_ref = config.approval_ref
        self.source_revision = config.source_revision

    async def readiness(self, *, principal: Principal) -> dict[str, Any]:
        result = await super().readiness(principal=principal)
        result["blockers"][0] = "s3_separate_operator_worker_required"
        return result

    async def check_binding(self) -> None:
        async with self.store.sessions() as session:
            heads = (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
            if list(heads) != [S3_MIGRATION]:
                raise CommandRuntimeError("s3_stage_schema_mismatch")
            binding = await session.scalar(
                select(RuntimeBindingRow.id).where(
                    RuntimeBindingRow.id == self.scope.binding_id,
                    RuntimeBindingRow.account_id == str(self.scope.account_id),
                    RuntimeBindingRow.workspace == self.scope.workspace,
                    RuntimeBindingRow.owner_subject == self.scope.owner_subject,
                    RuntimeBindingRow.custody_state == "prepared",
                    RuntimeBindingRow.session_generation == 1,
                )
            )
            if binding is None:
                raise CommandRuntimeError("s3_prepared_binding_required")
            account = await session.scalar(
                select(Account.id).where(
                    Account.id == self.scope.account_id,
                    Account.telegram_user_id == self.peer.provider_id,
                    func.lower(Account.owner).in_(("mark", "mark-personal")),
                    Account.is_active.is_(True),
                    Account.send_enabled.is_(True),
                )
            )
            if account is None:
                raise CommandRuntimeError("s3_account_pin_mismatch")

    async def check_queued(self, approval: SavedMessagesApproval) -> datetime:
        now = utc_now()
        if (
            approval.approval_ref != self.approval_ref
            or approval.source_revision != self.source_revision
        ):
            raise CommandRuntimeError("s3_permit_scope_mismatch")
        if not now < approval.expires_at <= self.grant.expires_at:
            raise CommandRuntimeError("s3_approval_window_invalid")
        async with self.store.sessions.begin() as session:
            row = await session.get(RuntimeOperationRow, approval.operation_id)
            if (
                row is None
                or row.binding_id != self.scope.binding_id
                or row.owner_subject != self.scope.owner_subject
                or row.client_namespace != self.grant.client_namespace
                or row.delegation_id != approval.delegation_id
                or row.delegation_id != self.grant.delegation_id
                or row.capability != "send_text"
                or row.peer_key != self.peer.key
                or row.state != "queued"
                or _utc(row.expires_at) <= now
            ):
                raise CommandRuntimeError("s3_approved_operation_not_queued")
            payload = self.payload_cipher.unseal(self.scope, row.id, row.payload_ciphertext)
            message = payload.get("text")
            if (
                set(payload) != {"text"}
                or not isinstance(message, str)
                or hashlib.sha256(message.encode()).hexdigest() != approval.text_sha256
            ):
                raise CommandRuntimeError("s3_approved_text_mismatch")
            await self.grants.for_operation(
                session,
                owner_subject=row.owner_subject,
                client_namespace=row.client_namespace,
                delegation_id=row.delegation_id,
                capability=row.capability,
                peer=self.peer,
                now=now,
                approval_ref=approval.approval_ref,
            )
        permit_deadline = await self.store.install_permit(
            approval.operation_id,
            permit=approval.permit(),
            now=now,
        )
        return min(permit_deadline, approval.expires_at, _utc(row.expires_at))


async def run_stage(
    action: Literal["validate", "preflight", "provision", "run-one"],
    config: S3StageSettings,
    environment: Mapping[str, str],
    *,
    approval: SavedMessagesApproval | None = None,
) -> dict[str, Any]:
    config.require_enabled()
    config.require_source(environment)
    if action == "validate":
        return {
            "account": "RU",
            "state": "configuration_valid",
            "source_revision": config.source_revision,
            "provider_calls": 0,
        }
    if action == "run-one":
        if approval is None:
            raise CommandRuntimeError("s3_exact_operation_required")
        if environment.get("GATE_PERSONAL_RUNTIME_ENABLED") != "true":
            raise CommandRuntimeError("s3_worker_disabled")
    dsn = config.database_url.get_secret_value()
    async with database_runtime_lifespan(dsn) as db:
        journal = S3StageJournal(db.session_factory, config)
        await journal.check_binding()
        if action == "preflight":
            return {
                "account": "RU",
                "state": "journal_ready",
                "source_revision": config.source_revision,
                "provider_calls": 0,
            }
        if action == "provision":
            await journal.grants.install_approved_grant(
                journal.grant, approval_ref=config.approval_ref, now=utc_now()
            )
            return {
                "account": "RU",
                "state": "grant_prepared",
                "source_revision": config.source_revision,
                "provider_calls": 0,
            }
        if approval is None:
            raise CommandRuntimeError("s3_exact_operation_required")
        deadline = await journal.check_queued(approval)
        vault = open_runtime_s2_session(environment)
        runtime = RuntimeSettings(expected_provider_owner=vault.owner_user_id)  # type: ignore[call-arg]
        if (
            not runtime.enabled
            or runtime.scope() != journal.scope
            or runtime.database_url.get_secret_value() != dsn
            or vault.api_id != runtime.api_id
            or vault.owner_user_id != config.expected_provider_owner
        ):
            raise CommandRuntimeError("s3_worker_binding_mismatch")
        seconds = min(300, (deadline - utc_now()).total_seconds())
        if seconds <= 0:
            raise CommandRuntimeError("s3_approval_window_invalid")
        application = TelegramApplication(runtime.api_id, runtime.api_hash.get_secret_value())
        application.validate()
        worker = RuCloudWorker(
            store=journal.store,
            cipher=runtime.cipher(),
            session_generation=1,
            expected_provider_user_id=vault.owner_user_id,
            client_factory=lambda session: TelethonRuntimeClient(session, application),
            enabled=True,
            startup_timeout=min(10, seconds),
            saved_messages=SavedMessagesDispatcher(
                approval, SqlPersonalTelegramSendFence(db.session_factory, enabled=True)
            ),
        )
        outcome = await run_worker(worker, seconds)
        return {
            "account": "RU",
            "state": outcome.state,
            "error_code": outcome.code,
            "source_revision": config.source_revision,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "preflight", "provision", "run-one"))
    parser.add_argument("--operation-ref")
    parser.add_argument("--text-sha256")
    parser.add_argument("--expires-at")
    parser.add_argument("--approval-ref")
    parser.add_argument("--source-revision")
    args = parser.parse_args()
    try:
        config = load_s3_stage()
        approval = None
        if args.action == "run-one":
            if (
                args.operation_ref is None
                or re.fullmatch(r"operation_[0-9a-f]{32}", args.operation_ref) is None
                or args.text_sha256 is None
                or args.expires_at is None
                or args.approval_ref is None
                or args.source_revision is None
            ):
                raise CommandRuntimeError("s3_exact_operation_required")
            approval = SavedMessagesApproval(
                UUID(hex=args.operation_ref.removeprefix("operation_")),
                config.grant_id,
                args.text_sha256,
                datetime.fromisoformat(args.expires_at),
                args.approval_ref,
                args.source_revision,
            )
        outcome = asyncio.run(run_stage(args.action, config, os.environ, approval=approval))
    except (CommandRuntimeError, PersonalTelegramTransportError) as exc:
        outcome = {"account": "RU", "state": "failed", "error_code": str(exc)}
    except (SQLAlchemyError, ValidationError, OSError, ValueError, TimeoutError):
        outcome = {"account": "RU", "state": "failed", "error_code": "s3_configuration_failed"}
    print(json.dumps(outcome, sort_keys=True))
    if outcome.get("state") in {"failed", "blocked"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
