"""Fail closed when durable ingress continuity is no longer healthy.

This loop never asserts new health or extends an evidence expiry. Only a
separately verified epoch can be renewed; a restart/gap stops old approvals.
"""

import asyncio
from uuid import UUID

from sqlalchemy import select

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
        control = await session.get(
            ReplyDispatchControl, runtime.workspace, with_for_update=True
        )
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
        if (
            connector is not None
            and connector.state == "healthy"
            and connector.continuity_until is not None
            and connector.continuity_until > now
        ):
            return 0
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
                    sequence = await session.get(
                        ReplySequence, identity, with_for_update=True
                    )
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


async def run_continuity_loop(runtime: ReplySequenceRuntime) -> None:
    """A failed DB visit never manufactures health; retry stops after recovery."""

    while True:
        try:
            await reconcile_continuity_once(runtime)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Claim/send also require the durable connector row and DB; a lost DB
            # prevents admission. Repeat the terminal stop once it is reachable.
            pass
        await asyncio.sleep(min(runtime.settings.gate_reply_sequence_poll_seconds, 300))
