"""Explicit, default-off import of complete legacy memberships; never sends."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.application.reply_sequence_policy import approval_digest, text_digest
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime, sequence_error
from app.models.domain import AuditLog, Conversation, ReplyDraft, SendAttempt
from app.models.reply_sequences import (
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplySequence,
    ReplySequenceItem,
)


async def initialize_control(runtime: ReplySequenceRuntime) -> None:
    async with runtime.sessions() as session:
        await session.execute(
            insert(ReplyDispatchControl)
            .values(
                workspace_ref=runtime.workspace,
                mode="disabled",
                generation=0,
                max_inflight=runtime.settings.gate_reply_sequence_max_inflight,
                account_cap=runtime.settings.gate_reply_sequence_max_inflight_per_account,
                bot_cap=runtime.settings.gate_reply_sequence_max_inflight_per_bot,
                migration_complete=False,
            )
            .on_conflict_do_nothing()
        )
        for slot in range(8):
            await session.execute(
                insert(ReplyDispatchPermit)
                .values(
                    workspace_ref=runtime.workspace,
                    slot=slot,
                    state="free",
                )
                .on_conflict_do_nothing()
            )
        await session.commit()


async def import_legacy(
    runtime: ReplySequenceRuntime, *, legacy_workers_drained: bool
) -> dict[str, int]:
    if not legacy_workers_drained or runtime.settings.gate_reply_sequence_worker_enabled:
        raise sequence_error("legacy_workers_not_drained")
    await initialize_control(runtime)
    result = {"imported": 0, "existing": 0, "quarantined": 0}
    cursor = ""
    while True:
        async with runtime.sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            assert control is not None
            if control.mode != "disabled":
                raise sequence_error("sequence_dispatch_not_disabled")
            if (
                await session.scalar(
                    select(SendAttempt.id)
                    .where(SendAttempt.status.in_({"reserved", "sending"}))
                    .limit(1)
                )
                is not None
            ):
                raise sequence_error("legacy_sender_inflight")
            ident = ReplyDraft.prompt_context["reply_sequence"]["id"].astext
            ids = (
                await session.scalars(
                    select(ident)
                    .where(
                        ReplyDraft.prompt_context["reply_sequence"]["workspace_id"].astext
                        == runtime.workspace,
                        ident > cursor,
                    )
                    .distinct()
                    .order_by(ident)
                    .limit(100)
                )
            ).all()
        if not ids:
            break
        for legacy_id in ids:
            cursor = legacy_id
            try:
                sequence_id = UUID(legacy_id)
            except ValueError:
                result["quarantined"] += await quarantine_legacy(runtime, legacy_id)
                continue
            async with runtime.sessions() as session:
                existing = await session.get(ReplySequence, sequence_id)
                if existing is not None:
                    if existing.workspace_ref != runtime.workspace:
                        result["quarantined"] += await quarantine_legacy(runtime, legacy_id)
                    else:
                        result["existing"] += 1
                    continue
                # Fetch full membership by identity, independent of discovery page boundaries.
                drafts = (
                    await session.scalars(
                        select(ReplyDraft).where(
                            ReplyDraft.prompt_context["reply_sequence"]["id"].astext == legacy_id,
                            ReplyDraft.prompt_context["reply_sequence"]["workspace_id"].astext
                            == runtime.workspace,
                        )
                    )
                ).all()
                if not drafts:
                    continue
                metadata = [d.prompt_context["reply_sequence"] for d in drafts]
                first = drafts[0]
                binding = (
                    first.account_id,
                    first.project_id,
                    first.reply_task_id,
                    first.conversation_id,
                )
                valid = (
                    2 <= len(drafts) <= 50
                    and all(
                        (d.account_id, d.project_id, d.reply_task_id, d.conversation_id) == binding
                        for d in drafts
                    )
                    and all(
                        m.get("workspace_id") == runtime.workspace
                        and m.get("size") == len(drafts)
                        and m.get("interval_seconds") == 60
                        and type(m.get("position")) is int
                        for m in metadata
                    )
                )
                positions = [m.get("position") for m in metadata]
                if valid:
                    valid = sorted(positions) == list(range(len(drafts)))
                conversation = await session.get(Conversation, first.conversation_id)
                if conversation is None:
                    valid = False
                if valid:
                    assert conversation is not None
                    valid = all(
                        m.get("expected_recipient_chat_id") == conversation.chat_id
                        for m in metadata
                    )
                if not valid:
                    result["quarantined"] += await quarantine_legacy(runtime, legacy_id)
                    continue
                assert conversation is not None
                account_id, chat_id = first.account_id, conversation.chat_id
            async with dialog_guard(
                runtime.ingress_guards,
                workspace=runtime.workspace,
                account_id=account_id,
                chat_id=chat_id,
            ):
                async with runtime.sessions() as session:
                    drafts = list(
                        (
                            await session.scalars(
                                select(ReplyDraft)
                                .where(
                                    ReplyDraft.prompt_context["reply_sequence"]["id"].astext
                                    == legacy_id,
                                    ReplyDraft.prompt_context["reply_sequence"][
                                        "workspace_id"
                                    ].astext
                                    == runtime.workspace,
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    drafts.sort(key=lambda d: d.prompt_context["reply_sequence"]["position"])
                    if await session.get(ReplySequence, sequence_id) is not None:
                        result["existing"] += 1
                        continue
                    attempts = {
                        a.reply_draft_id: a
                        for a in (
                            await session.scalars(
                                select(SendAttempt).where(
                                    SendAttempt.reply_draft_id.in_([d.id for d in drafts])
                                )
                            )
                        ).all()
                    }
                    if any(a.status in {"reserved", "sending"} for a in attempts.values()):
                        raise sequence_error("legacy_sender_inflight")
                    now = await runtime.db_now(session)
                    guard = await runtime.guard_row(session, account_id, chat_id)
                    first = drafts[0]
                    hashes = [(d.id, text_digest(d.draft_text)) for d in drafts]
                    complete = all(
                        d.id in attempts
                        and attempts[d.id].status == "sent"
                        and attempts[d.id].finished_at is not None
                        for d in drafts
                    )
                    sequence = ReplySequence(
                        id=sequence_id,
                        guard_id=guard.id,
                        workspace_ref=runtime.workspace,
                        account_id=first.account_id,
                        project_id=first.project_id,
                        task_id=first.reply_task_id,
                        conversation_id=first.conversation_id,
                        principal_subject=str(
                            first.prompt_context["reply_sequence"].get(
                                "principal_subject", "legacy"
                            )
                        ),
                        account_label=str(
                            first.prompt_context["reply_sequence"].get("expected_account", "")
                        ),
                        bot_resource_id=runtime.bot_resource,
                        state="completed" if complete else "stopped",
                        stop_reason=None if complete else "legacy_reapproval_required",
                        stopped_at=None if complete else now,
                        queued_revision=guard.inbound_revision,
                        item_count=len(drafts),
                        next_position=len(drafts) if complete else 0,
                        next_due_at=now,
                        approved_at=now,
                        approval_digest=approval_digest(
                            workspace=runtime.workspace,
                            account=first.account_id,
                            project=first.project_id,
                            task=first.reply_task_id,
                            conversation=first.conversation_id,
                            recipient=chat_id,
                            revision=guard.inbound_revision,
                            drafts=hashes,
                        ),
                    )
                    session.add(sequence)
                    await session.flush()
                    items = []
                    for position, draft in enumerate(drafts):
                        attempt = attempts.get(draft.id)
                        state = (
                            attempt.status
                            if attempt and attempt.status in {"sent", "failed", "unknown"}
                            else "cancelled"
                        )
                        item = ReplySequenceItem(
                            sequence_id=sequence.id,
                            position=position,
                            draft_id=draft.id,
                            scheduled_at=now + timedelta(seconds=position * 60),
                            state=state,
                            attempt_id=attempt.id if attempt else None,
                            finished_at=attempt.finished_at if attempt else now,
                            safe_error_code=attempt.error_code
                            if attempt
                            else "legacy_reapproval_required",
                            approved_text_sha256=text_digest(draft.draft_text),
                        )
                        session.add(item)
                        items.append(item)
                        context = dict(draft.prompt_context)
                        context["reply_sequence"] = {
                            **context["reply_sequence"],
                            "authority_version": 2,
                        }
                        draft.prompt_context = context
                    await runtime.project(session, items)
                    await session.commit()
                    result["imported"] += 1
    async with runtime.sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace, with_for_update=True)
        assert control is not None
        if control.mode != "disabled":
            raise sequence_error("sequence_dispatch_not_disabled")
        if not control.migration_complete:
            control.migration_complete = True
            control.generation += 1
        await session.commit()
    return result


async def quarantine_legacy(runtime: ReplySequenceRuntime, identity: str) -> int:
    async with runtime.sessions() as session:
        drafts = (
            await session.scalars(
                select(ReplyDraft)
                .where(
                    ReplyDraft.prompt_context["reply_sequence"]["id"].astext == identity,
                    ReplyDraft.prompt_context["reply_sequence"]["workspace_id"].astext
                    == runtime.workspace,
                )
                .with_for_update()
            )
        ).all()
        for draft in drafts:
            metadata = draft.prompt_context["reply_sequence"]
            context = dict(draft.prompt_context)
            state = metadata.get("state")
            context["reply_sequence"] = {
                **metadata,
                "authority_version": 2,
                "state": state if state in {"sent", "unknown", "failed"} else "cancelled",
                "error_code": "legacy_integrity_invalid",
            }
            draft.prompt_context = context
        session.add(
            AuditLog(
                actor="reply-sequence-import",
                action="reply_sequence_legacy_quarantined",
                metadata_={"draft_count": len(drafts)},
            )
        )
        await session.commit()
        return 1
