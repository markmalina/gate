"""Explicit operator CLI for default-off sequence import and admission control.

No provider requests are made. Production use requires the approved rollout
scope and verified legacy drain/ingress continuity described in the runbook.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_migration import import_legacy, initialize_control
from app.infrastructure.reply_sequence_operations import (
    CanaryArm,
    CanaryGrant,
    arm_canary,
    canary_status,
    close_canary,
)
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime, sequence_error
from app.models.domain import AuditLog
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplySequence,
)


async def execute(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    if settings.gate_reply_sequence_db_pool_mode not in {"direct", "session"}:
        raise ValueError("Verify direct/session pooling before running sequence control")
    if not args.bot_resource_id:
        raise ValueError("verified_bot_resource_required")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    db = DatabaseRuntime(engine, async_sessionmaker(engine, expire_on_commit=False))
    runtime = ReplySequenceRuntime(
        engine=engine,
        settings=settings,
        sessions=db.session_factory,
        bot_resource_id=args.bot_resource_id,
    )
    try:
        if args.action == "epoch-arm":
            from app.infrastructure.reply_sequence_epoch import install_verified_epoch

            raw = args.protected_input
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ValueError("scope_too_large")
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {
                "evidence_sha256",
                "verified_at",
                "members",
                "binding_sha256",
                "expected_connector_version",
            }:
                raise ValueError("epoch_fields_invalid")
            async with db.session_factory() as session:
                epoch_id = await install_verified_epoch(
                    session,
                    workspace=runtime.workspace,
                    bot_resource_id=runtime.bot_resource,
                    evidence_sha256=value["evidence_sha256"],
                    verified_at=datetime.fromisoformat(value["verified_at"]),
                    members=value["members"],
                    binding_sha256=value["binding_sha256"],
                    expected_connector_version=value["expected_connector_version"],
                )
                await session.commit()
                return {"schema_version": "gate.sequence-control.v1", "epoch_id": str(epoch_id)}
        if args.action == "canary-status":
            return await canary_status(runtime)
        if args.action in {"canary-close", "canary-reconcile"}:
            return await close_canary(runtime, execution_id=args.execution_id)
        if args.action == "canary-arm":
            return await arm_canary(runtime, parse_canary_arm(args.protected_input))
        if args.action == "import-legacy":
            return dict(
                await import_legacy(runtime, legacy_workers_drained=args.legacy_workers_drained)
            )
        if args.action == "initialize":
            await initialize_control(runtime)
            return {"mode": "disabled", "initialized": True}
        if args.action == "stop-active":
            async with db.session_factory() as session:
                control = await session.get(
                    ReplyDispatchControl, runtime.workspace, with_for_update=True
                )
                if control is None:
                    raise sequence_error("sequence_migration_incomplete")
                control.mode = "disabled"
                control.generation += 1
                await session.commit()
                rows = (
                    await session.execute(
                        select(
                            ReplySequence.id,
                            ReplySequence.account_id,
                            ReplyDialogGuard.provider_chat_id,
                        )
                        .join(ReplyDialogGuard)
                        .where(
                            ReplySequence.workspace_ref == runtime.workspace,
                            ReplySequence.state == "active",
                        )
                    )
                ).all()
            stopped = 0
            for identity, account, chat in rows:
                async with dialog_guard(
                    runtime.ingress_guards,
                    workspace=runtime.workspace,
                    account_id=account,
                    chat_id=chat,
                ):
                    async with db.session_factory() as session:
                        sequence = await session.get(ReplySequence, identity, with_for_update=True)
                        if sequence is not None and sequence.state == "active":
                            await runtime.stop(
                                session,
                                sequence,
                                reason="ingress_continuity_lost",
                                now=await runtime.db_now(session),
                                notify=True,
                            )
                            stopped += 1
                        await session.commit()
            return {"mode": "disabled", "stopped": stopped}
        async with db.session_factory() as session:
            control = await session.get(
                ReplyDispatchControl, runtime.workspace, with_for_update=True
            )
            if control is None:
                raise sequence_error("sequence_migration_incomplete")
            if args.action == "status":
                occupied = (
                    await session.scalars(
                        select(ReplyDispatchPermit).where(
                            ReplyDispatchPermit.workspace_ref == runtime.workspace,
                            ReplyDispatchPermit.state != "free",
                        )
                    )
                ).all()
                active = await session.scalar(
                    select(func.count())
                    .select_from(ReplySequence)
                    .where(
                        ReplySequence.workspace_ref == runtime.workspace,
                        ReplySequence.state == "active",
                    )
                )
                oldest = await session.scalar(
                    select(func.min(ReplySequence.next_due_at)).where(
                        ReplySequence.workspace_ref == runtime.workspace,
                        ReplySequence.state == "active",
                    )
                )
                latest = await session.scalar(
                    select(func.max(ReplySequence.last_admitted_at)).where(
                        ReplySequence.workspace_ref == runtime.workspace
                    )
                )
                connector = await session.scalar(
                    select(ReplyConnectorAdmission).where(
                        ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                        ReplyConnectorAdmission.connector_key == "telegram_business",
                    )
                )
                now = await runtime.db_now(session)
                healthy = bool(
                    connector
                    and connector.epoch_id is not None
                    and connector.state == "healthy"
                    and connector.continuity_until
                    and connector.continuity_until > now
                )
                capacity = control.max_inflight if control.mode == "parallel" else 1
                from app.infrastructure.reply_sequence_epoch import binding_digest
                from app.models.reply_sequences import ReplyIngressProcess

                processes = (
                    await session.scalars(
                        select(ReplyIngressProcess).where(
                            ReplyIngressProcess.workspace_ref == runtime.workspace
                        )
                    )
                ).all()
                return {
                    "schema_version": "gate.sequence-control.v1",
                    "observed_at": now.isoformat(),
                    "bot_resource_id": runtime.bot_resource,
                    "binding_sha256": await binding_digest(session),
                    "connector_version": connector.version if connector else None,
                    "epoch_id": str(connector.epoch_id)
                    if connector and connector.epoch_id
                    else None,
                    "continuity_until": connector.continuity_until.isoformat()
                    if connector and connector.continuity_until
                    else None,
                    "fleet_observations": [
                        {
                            "boot_id": p.boot_id,
                            "source_sha": p.source_sha,
                            "config_sha256": p.config_sha256,
                            "bot_resource_id": p.bot_resource_id,
                            "observed_at": p.observed_at.isoformat(),
                            "workers": p.workers,
                            "last_gap_at": p.last_gap_at.isoformat() if p.last_gap_at else None,
                        }
                        for p in processes
                    ],
                    "active": active,
                    "oldest_due_at": oldest.isoformat() if oldest else None,
                    "last_admitted_at": latest.isoformat() if latest else None,
                    "ingress_healthy": healthy,
                    "degraded_reason": "ingress_unavailable"
                    if not healthy
                    else "permits_quarantined"
                    if sum(p.state == "quarantined" for p in occupied) >= capacity
                    else None,
                    "mode": control.mode,
                    "generation": control.generation,
                    "migration_complete": control.migration_complete,
                    "occupied": len(occupied),
                    "quarantined": sum(p.state == "quarantined" for p in occupied),
                }
            if args.action == "disable":
                control.mode = "disabled"
            else:
                if not args.legacy_workers_drained or not control.migration_complete:
                    raise sequence_error("legacy_workers_not_drained")
                if (
                    not settings.gate_reply_sequence_stop_on_inbound_enabled
                    or not settings.gate_reply_sequence_dispatch_v2_enabled
                ):
                    raise sequence_error("sequence_guard_unavailable")
                if args.action == "parallel" and not settings.gate_reply_sequence_parallel_enabled:
                    raise sequence_error("sequence_dispatch_disabled")
                # Activation consumes an already installed verified epoch; it
                # never manufactures health or extends expiry from a CLI string.
                prior = await session.scalar(
                    select(ReplyConnectorAdmission)
                    .where(
                        ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                        ReplyConnectorAdmission.connector_key == "telegram_business",
                    )
                    .with_for_update()
                )
                now = await runtime.db_now(session)
                if (
                    prior is None
                    or prior.epoch_id is None
                    or prior.state != "healthy"
                    or prior.continuity_until is None
                    or prior.continuity_until <= now
                ):
                    raise sequence_error("sequence_guard_unavailable")
                from app.infrastructure.reply_sequence_epoch import renew_verified_epoch
                from app.models.reply_sequences import ReplyContinuityEpoch

                epoch = await session.get(ReplyContinuityEpoch, prior.epoch_id)
                if epoch is None or epoch.bot_resource_id != runtime.bot_resource:
                    raise sequence_error("sequence_bot_identity_mismatch")

                await renew_verified_epoch(session, connector=prior, now=now)
                if settings.gate_reply_sequence_canary_enabled:
                    from app.models.reply_sequences import ReplyCanaryScope

                    scope = await session.get(ReplyCanaryScope, runtime.workspace)
                    if (
                        scope is None
                        or scope.state != "active"
                        or scope.expires_at <= now
                        or scope.attempts_reserved >= scope.max_attempts
                    ):
                        raise sequence_error("canary_admission_closed")
                control.mode = args.action
            control.generation += 1
            session.add(
                AuditLog(
                    actor="reply-sequence-control",
                    action="reply_sequence_mode_changed",
                    metadata_={
                        "workspace_ref": runtime.workspace,
                        "mode": control.mode,
                        "generation": control.generation,
                    },
                )
            )
            await session.commit()
            return {"mode": control.mode, "generation": control.generation}
    finally:
        await runtime.close()
        await db.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "initialize",
            "import-legacy",
            "status",
            "disable",
            "stop-active",
            "serial",
            "parallel",
            "epoch-arm",
            "canary-arm",
            "canary-status",
            "canary-close",
            "canary-reconcile",
        ],
    )
    parser.add_argument("--legacy-workers-drained", action="store_true")
    parser.add_argument("--bot-resource-id", required=True)
    parser.add_argument("--execution-id")
    args = parser.parse_args()
    args.protected_input = (
        sys.stdin.read(65537) if args.action in {"canary-arm", "epoch-arm"} else None
    )
    try:
        result = asyncio.run(execute(args))
    except Exception:
        # Never print SQLAlchemy/validation/provider errors containing protected inputs.
        print(
            json.dumps(
                {
                    "schema_version": "gate.sequence-control.v1",
                    "ok": False,
                    "error_code": "sequence_control_refused",
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


def parse_canary_arm(raw: str) -> CanaryArm:
    if len(raw) > 65536:
        raise ValueError("scope_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "execution_id",
        "principal_subject",
        "expected_generation",
        "bot_resource_id",
        "grants",
        "notification_destination",
        "expires_at",
        "max_attempts",
    }:
        raise ValueError("scope_fields_invalid")
    if not isinstance(value["grants"], list) or any(
        not isinstance(g, dict) or set(g) != {"account_id", "recipient", "draft_id", "body_sha256"}
        for g in value["grants"]
    ):
        raise ValueError("scope_grants_invalid")
    return CanaryArm(
        execution_id=value["execution_id"],
        principal_subject=value["principal_subject"],
        expected_generation=value["expected_generation"],
        bot_resource_id=value["bot_resource_id"],
        notification_destination=value["notification_destination"],
        expires_at=datetime.fromisoformat(value["expires_at"]),
        max_attempts=value["max_attempts"],
        grants=tuple(
            CanaryGrant(
                account_id=UUID(g["account_id"]),
                recipient=g["recipient"],
                draft_id=UUID(g["draft_id"]),
                body_sha256=g["body_sha256"],
            )
            for g in value["grants"]
        ),
    )


if __name__ == "__main__":
    main()
