"""Explicit operator CLI for default-off sequence import and admission control.

No provider requests are made. Production use requires the approved rollout
scope and verified legacy drain/ingress continuity described in the runbook.
"""

import argparse
import asyncio
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.application.reply_sequence_policy import text_digest
from app.core.config import get_settings
from app.infrastructure.database import build_database_runtime
from app.infrastructure.reply_sequence_migration import import_legacy, initialize_control
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime, sequence_error
from app.models.domain import AuditLog
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDispatchControl,
    ReplyDispatchPermit,
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
                return {
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
        "action", choices=["initialize", "import-legacy", "status", "disable", "serial", "parallel"]
    )
    parser.add_argument("--legacy-workers-drained", action="store_true")
    parser.add_argument("--continuity-until")
    parser.add_argument("--continuity-evidence-ref")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute(args)), sort_keys=True))


if __name__ == "__main__":
    main()
