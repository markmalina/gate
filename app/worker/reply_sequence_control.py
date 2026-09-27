"""Explicit operator CLI for default-off sequence import and admission control.

No provider requests are made. Production use requires the approved rollout
scope and verified legacy drain/ingress continuity described in the runbook.
"""

import argparse
import asyncio
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.application.reply_sequence_policy import text_digest
from app.core.config import get_settings
from app.infrastructure.database import build_database_runtime
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_migration import import_legacy, initialize_control
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
    db = build_database_runtime(settings.database_url)
    runtime = db.sequences or ReplySequenceRuntime(
        engine=db.engine, settings=settings, sessions=db.session_factory
    )
    try:
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
                    and connector.state == "healthy"
                    and connector.continuity_until
                    and connector.continuity_until > now
                )
                capacity = control.max_inflight if control.mode == "parallel" else 1
                return {
                    "observed_at": now.isoformat(),
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
                if not args.continuity_until or not args.continuity_evidence_ref:
                    raise ValueError("Activation requires fresh scoped ingress continuity evidence")
                until = datetime.fromisoformat(args.continuity_until)
                now = await runtime.db_now(session)
                if until.tzinfo is None or not 0 < (until - now).total_seconds() <= 3600:
                    raise ValueError("Continuity evidence must expire within one hour")
                prior = await session.scalar(
                    select(ReplyConnectorAdmission)
                    .where(
                        ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                        ReplyConnectorAdmission.connector_key == "telegram_business",
                    )
                    .with_for_update()
                )
                if (
                    prior is None
                    or prior.state != "healthy"
                    or prior.continuity_until is None
                    or prior.continuity_until <= now
                ):
                    if (
                        await session.scalar(
                            select(ReplySequence.id)
                            .where(
                                ReplySequence.workspace_ref == runtime.workspace,
                                ReplySequence.state == "active",
                            )
                            .limit(1)
                        )
                        is not None
                    ):
                        raise sequence_error("sequence_continuity_reapproval_required")
                from uuid import uuid4

                await session.execute(
                    insert(ReplyConnectorAdmission)
                    .values(
                        id=uuid4(),
                        workspace_ref=runtime.workspace,
                        connector_key="telegram_business",
                        state="healthy",
                        version=1,
                        observed_at=now,
                        continuity_until=until,
                    )
                    .on_conflict_do_update(
                        index_elements=["workspace_ref", "connector_key"],
                        set_={
                            "state": "healthy",
                            "safe_reason": None,
                            "observed_at": now,
                            "continuity_until": until,
                            "version": ReplyConnectorAdmission.version + 1,
                        },
                    )
                )
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
                        "continuity_evidence_digest": text_digest(args.continuity_evidence_ref)
                        if args.continuity_evidence_ref
                        else None,
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
        ],
    )
    parser.add_argument("--legacy-workers-drained", action="store_true")
    parser.add_argument("--continuity-until")
    parser.add_argument("--continuity-evidence-ref")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute(args)), sort_keys=True))


if __name__ == "__main__":
    main()
