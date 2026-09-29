"""Fail closed when durable ingress continuity is no longer healthy.

Renewal consumes real process/receipt observations within a separately verified
fleet epoch; a restart/gap stops old approvals and never manufactures new health.
"""

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.application.worker_health import WorkerHealthRegistry
from app.infrastructure.reply_sequence_epoch import (
    ContinuityDenied,
    observe_process,
    renew_verified_epoch,
)
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplySequence,
)


async def reconcile_continuity_once(runtime: ReplySequenceRuntime) -> int:
    """Fence new admissions first; then stop every remaining active sequence."""

    async with runtime.sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace, with_for_update=True)
        if control is None:
            return 0  # Schema/import not initialized; DB admission is already closed.
        now = await runtime.db_now(session)
        connector = await session.scalar(
            select(ReplyConnectorAdmission)
            .where(
                ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                ReplyConnectorAdmission.connector_key == "telegram_business",
            )
            .with_for_update()
        )
        try:
            if connector is None:
                raise ContinuityDenied("continuity_connector_missing")
            await renew_verified_epoch(session, connector=connector, now=now)
            await session.commit()
            return 0
        except ContinuityDenied as exc:
            if connector is not None and connector.state == "healthy":
                connector.state = "blocked"
                connector.safe_reason = str(exc)
                connector.continuity_until = None
                connector.version += 1
        if control.mode != "disabled":
            control.mode = "disabled"
            control.generation += 1
        await session.commit()

    stopped = 0
    cursor: UUID | None = None
    while True:
        async with runtime.sessions() as session:
            query = (
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
                .order_by(ReplySequence.id)
                .limit(100)
            )
            if cursor is not None:
                query = query.where(ReplySequence.id > cursor)
            rows = (await session.execute(query)).all()
        if not rows:
            break
        for identity, account, chat in rows:
            cursor = identity
            async with dialog_guard(
                runtime.ingress_guards,
                workspace=runtime.workspace,
                account_id=account,
                chat_id=chat,
            ):
                async with runtime.sessions() as session:
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
    return stopped


async def run_continuity_loop(
    runtime: ReplySequenceRuntime,
    *,
    health_registry: WorkerHealthRegistry | None = None,
) -> None:
    """A failed DB visit never manufactures health; retry stops after recovery."""

    while True:
        try:
            if health_registry is not None:
                await observe_process(runtime, health_registry)
            from app.infrastructure.reply_sequence_operations import reconcile_canary_once

            await reconcile_canary_once(runtime)
            await reconcile_continuity_once(runtime)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Claim/send also require the durable connector row and DB; a lost DB
            # prevents admission. Repeat the terminal stop once it is reachable.
            pass
        await asyncio.sleep(min(runtime.settings.gate_reply_sequence_poll_seconds, 30))
