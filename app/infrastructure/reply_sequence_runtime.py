"""Shared durable sequence queue, early inbound stop and dispatch authority."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, true, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.application.reply_sequence_cooldown import (
    ReplySequenceCooldown,
    extend_reply_sequence_cooldown,
)
from app.application.reply_sequence_policy import (
    BusinessEnvelope,
    approval_digest,
    business_envelope,
    text_digest,
)
from app.application.reply_sequences import (
    QueueApprovedReplySequenceCommand,
    QueuedReplySequence,
    ReplySequenceError,
)
from app.application.telegram_processing import AcceptTelegramReceiptCommand, TelegramReceiptError
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sequence_context import approval_context_state
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_recipient import recipient_display
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    ReplyDraft,
    SendAttempt,
    TelegramBusinessConnection,
)
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplyInboundSignal,
    ReplySequence,
    ReplySequenceItem,
    ReplySequenceNotification,
    ReplyTransportBudget,
)

RUNTIME_KEY = "reply_sequence_runtime"


def runtime_for_session(session: AsyncSession) -> ReplySequenceRuntime | None:
    value = getattr(session, "info", {}).get(RUNTIME_KEY)
    return value if isinstance(value, ReplySequenceRuntime) else None


def sequence_error(code: str) -> ReplySequenceError:
    return ReplySequenceError(code=code, message=code.replace("_", " "))


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    sequence_id: UUID
    token: UUID
    draft_id: UUID
    account_id: UUID
    project_id: UUID
    task_id: UUID
    conversation_id: UUID
    workspace: str
    recipient: str
    account_label: str
    principal_subject: str
    position: int
    generation: int
    canary_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class IngressBinding:
    envelope: BusinessEnvelope
    account_id: UUID | None
    direction: str


class ReplySequenceRuntime:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        sender_guards: AsyncEngine | None = None,
        ingress_guards: AsyncEngine | None = None,
        bot_resource_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.workspace = settings.gate_workspace_id
        self.owner = uuid4().hex
        self.cursor: tuple[datetime, UUID] | None = None
        self.recovery_cursor: UUID | None = None
        self.shutdown_supervisor: Any = None
        self.sender_guards = sender_guards or create_async_engine(
            engine.url,
            pool_size=settings.gate_reply_sequence_max_inflight,
            max_overflow=0,
            pool_timeout=2,
            pool_pre_ping=True,
            isolation_level="AUTOCOMMIT",
        )
        self.ingress_guards = ingress_guards or create_async_engine(
            engine.url,
            pool_size=2,
            max_overflow=0,
            pool_timeout=2,
            pool_pre_ping=True,
            isolation_level="AUTOCOMMIT",
        )
        # Token prefix is Telegram's stable bot identity; credentials are never persisted.
        prefix = settings.telegram_bot_token.split(":", 1)[0]
        if bot_resource_id is not None:
            if len(bot_resource_id) != 64 or any(
                c not in "0123456789abcdef" for c in bot_resource_id
            ):
                raise ValueError("Sequence control requires verified bot resource digest")
            if prefix and text_digest("telegram-bot:" + prefix) != bot_resource_id:
                raise ValueError("Sequence bot identity mismatch")
            self.bot_resource = bot_resource_id
        else:
            if not prefix.isdecimal() or int(prefix) <= 0:
                raise ValueError("Sequence runtime requires a valid bot identity")
            self.bot_resource = text_digest("telegram-bot:" + prefix)
        self.bot_prefix = prefix
        self.transport_gate: Any = None

    def register_transport_gate(self) -> None:
        if not self.bot_prefix:
            raise ValueError("Control runtime cannot register a Telegram transport")
        from app.infrastructure.reply_transport_budget import SqlTelegramTransportBudget
        from app.services.telegram_delivery_gate import register_gate, registered_gate

        # The web lifespan can already own the same process-wide bot budget.
        # A nested worker never replaces it or unregisters somebody else's gate.
        if registered_gate(self.bot_prefix) is not None:
            return
        self.transport_gate = SqlTelegramTransportBudget(self)
        register_gate(self.bot_prefix, self.transport_gate)

    async def close(self) -> None:
        from app.services.telegram_delivery_gate import unregister_gate

        if self.transport_gate is not None:
            unregister_gate(self.bot_prefix, self.transport_gate)
        await self.sender_guards.dispose()
        await self.ingress_guards.dispose()

    async def invalidate_ingress(self, reason: str) -> None:
        async with self.sessions() as session:
            now = await self.db_now(session)
            await session.execute(
                insert(ReplyConnectorAdmission)
                .values(
                    id=uuid4(),
                    workspace_ref=self.workspace,
                    connector_key="telegram_business",
                    state="reconciling",
                    safe_reason=reason,
                    version=1,
                    observed_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["workspace_ref", "connector_key"],
                    set_={
                        "state": "reconciling",
                        "safe_reason": reason,
                        "observed_at": now,
                        "continuity_until": None,
                        "version": ReplyConnectorAdmission.version + 1,
                    },
                )
            )
            await session.commit()

    async def db_now(self, session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise RuntimeError("sequence_clock_unavailable")
        return value

    async def guard_row(
        self, session: AsyncSession, account_id: UUID, chat_id: str
    ) -> ReplyDialogGuard:
        await session.execute(
            insert(ReplyDialogGuard)
            .values(
                id=uuid4(),
                workspace_ref=self.workspace,
                account_id=account_id,
                provider_chat_id=chat_id,
                inbound_revision=0,
                health_state="healthy",
                guard_version=0,
            )
            .on_conflict_do_nothing()
        )
        result = await session.scalar(
            select(ReplyDialogGuard)
            .where(
                ReplyDialogGuard.workspace_ref == self.workspace,
                ReplyDialogGuard.account_id == account_id,
                ReplyDialogGuard.provider_chat_id == chat_id,
            )
            .with_for_update()
        )
        assert result is not None
        return result

    async def control(self, session: AsyncSession, *, lock: bool = False) -> ReplyDispatchControl:
        query = select(ReplyDispatchControl).where(
            ReplyDispatchControl.workspace_ref == self.workspace
        )
        row = await session.scalar(query.with_for_update(read=not lock))
        if row is None or not row.migration_complete:
            raise sequence_error("sequence_migration_incomplete")
        if row.mode == "disabled":
            raise sequence_error("sequence_dispatch_disabled")
        if (
            row.max_inflight != self.settings.gate_reply_sequence_max_inflight
            or row.account_cap != self.settings.gate_reply_sequence_max_inflight_per_account
            or row.bot_cap != self.settings.gate_reply_sequence_max_inflight_per_bot
            or (row.mode == "parallel" and not self.settings.gate_reply_sequence_parallel_enabled)
        ):
            raise sequence_error("sequence_config_mismatch")
        return row

    async def healthy(self, session: AsyncSession, now: datetime) -> None:
        connector = await session.scalar(
            select(ReplyConnectorAdmission)
            .where(
                ReplyConnectorAdmission.workspace_ref == self.workspace,
                ReplyConnectorAdmission.connector_key == "telegram_business",
            )
            .with_for_update(read=True)
        )
        if (
            connector is None
            or connector.epoch_id is None
            or connector.state != "healthy"
            or connector.continuity_until is None
            or connector.continuity_until <= now
        ):
            raise sequence_error("sequence_guard_unavailable")

    async def queue(
        self,
        session: AsyncSession,
        command: QueueApprovedReplySequenceCommand,
        writer: Callable[[QueueApprovedReplySequenceCommand], Awaitable[QueuedReplySequence]],
    ) -> QueuedReplySequence:
        if not self.settings.gate_reply_sequence_dispatch_v2_enabled:
            raise sequence_error("sequence_dispatch_disabled")
        if (
            type(command.expected_inbound_revision) is not int
            or command.expected_inbound_revision < 0
        ):
            raise sequence_error("inbound_revision_required")
        scope = command.principal.access_scope
        if scope.workspace_id != self.workspace:
            raise sequence_error("invalid_access_scope")
        row = (
            await session.execute(
                select(ReplyDraft.account_id, Conversation.chat_id, ReplyDraft.reply_task_id)
                .join(Conversation, ReplyDraft.conversation_id == Conversation.id)
                .where(
                    ReplyDraft.id == command.draft_ids[0],
                    ReplyDraft.account_id.in_(scope.account_ids),
                    ReplyDraft.project_id.in_(scope.project_ids),
                )
            )
        ).first()
        await session.rollback()
        if row is None:
            raise sequence_error("draft_not_found")
        account_id, chat, task_id = row
        if chat != command.expected_recipient_chat_id:
            raise sequence_error("recipient_confirmation_mismatch")
        async with dialog_guard(
            self.ingress_guards, workspace=self.workspace, account_id=account_id, chat_id=chat
        ):
            try:
                now = await self.db_now(session)
                control = await self.control(session)
                await self.healthy(session, now)
                guard = await self.guard_row(session, account_id, chat)
                if guard.health_state != "healthy":
                    raise sequence_error("sequence_guard_unavailable")
                if guard.inbound_revision != command.expected_inbound_revision:
                    raise sequence_error("recipient_context_changed")
                context_state = await approval_context_state(session, scope, task_id)
                if context_state is None or context_state.revision != guard.inbound_revision:
                    raise sequence_error("recipient_context_changed")
                if context_state.blocked_reason:
                    raise sequence_error(context_state.blocked_reason)
                active = await session.scalar(
                    select(ReplySequence.id).where(
                        ReplySequence.guard_id == guard.id, ReplySequence.state == "active"
                    )
                )
                if active is not None:
                    raise sequence_error("sequence_already_active")
                queued = await writer(command)
                drafts = {
                    d.id: d
                    for d in (
                        await session.scalars(
                            select(ReplyDraft).where(ReplyDraft.id.in_(command.draft_ids))
                        )
                    ).all()
                }
                hashes = [(d, text_digest(drafts[d].draft_text)) for d in command.draft_ids]
                from app.infrastructure.reply_sequence_canary import CanaryDenied
                from app.infrastructure.reply_sequence_operations import admit_canary_queue

                try:
                    canary_execution = await admit_canary_queue(
                        session,
                        runtime=self,
                        principal=command.principal.subject,
                        account_id=queued.account_id,
                        recipient=chat,
                        drafts=hashes,
                        now=now,
                    )
                except CanaryDenied:
                    raise sequence_error("canary_admission_closed") from None
                sequence = ReplySequence(
                    id=queued.sequence_id,
                    guard_id=guard.id,
                    workspace_ref=self.workspace,
                    account_id=queued.account_id,
                    project_id=queued.project_id,
                    task_id=queued.task_id,
                    conversation_id=drafts[command.draft_ids[0]].conversation_id,
                    principal_subject=command.principal.subject,
                    account_label=queued.account_label,
                    bot_resource_id=self.bot_resource,
                    canary_execution_id=canary_execution,
                    queued_revision=guard.inbound_revision,
                    item_count=len(hashes),
                    next_due_at=now,
                    approved_at=now,
                    dispatch_generation=control.generation,
                    approval_digest=approval_digest(
                        workspace=self.workspace,
                        account=queued.account_id,
                        project=queued.project_id,
                        task=queued.task_id,
                        conversation=drafts[command.draft_ids[0]].conversation_id,
                        recipient=chat,
                        revision=guard.inbound_revision,
                        drafts=hashes,
                    ),
                )
                session.add(sequence)
                await session.flush()
                for item, (_, digest) in zip(queued.items, hashes, strict=True):
                    session.add(
                        ReplySequenceItem(
                            sequence_id=queued.sequence_id,
                            position=item.position,
                            draft_id=item.draft_id,
                            scheduled_at=item.scheduled_at,
                            approved_text_sha256=digest,
                        )
                    )
                    draft = drafts[item.draft_id]
                    metadata = dict(draft.prompt_context)
                    metadata["reply_sequence"] = {
                        **metadata["reply_sequence"],
                        "state": "managed_v2",
                        "authority_version": 2,
                    }
                    draft.prompt_context = metadata
                from app.infrastructure.reply_sequence_owner_route import owner_route

                route = await owner_route(
                    session,
                    account_id=queued.account_id,
                    principal_subject=command.principal.subject,
                    recipient=chat,
                )
                readiness = (
                    "disabled"
                    if not self.settings.gate_reply_sequence_notifications_enabled
                    else "ready"
                    if route is not None
                    else "blocked_route"
                )
                await session.commit()
                return replace(
                    queued,
                    queued_revision=guard.inbound_revision,
                    state="active",
                    notification_readiness=readiness,
                )
            except BaseException:
                await session.rollback()
                raise

    async def resolve_ingress(
        self, session: AsyncSession, command: AcceptTelegramReceiptCommand
    ) -> IngressBinding | None:
        if command.bot_key != "telegram_business":
            return None
        envelope = business_envelope(command.payload)
        if envelope is None:
            return None
        row = (
            await session.execute(
                select(TelegramBusinessConnection, Account)
                .join(Account, TelegramBusinessConnection.account_id == Account.id)
                .where(
                    TelegramBusinessConnection.business_connection_id == envelope.connection_id,
                )
            )
        ).first()
        if row is None:
            return IngressBinding(envelope, None, "unresolved")
        connection, account = row
        owners = {
            v for v in (connection.telegram_user_id, account.telegram_user_id) if v is not None
        }
        direction = "unresolved"
        if len(owners) == 1 and connection.is_enabled:
            owner = next(iter(owners))
            if envelope.sender_id == owner or (
                envelope.sender_is_bot and str(envelope.sender_id) == self.bot_prefix
            ):
                direction = "outbound"
            elif (
                not envelope.sender_is_bot
                and envelope.sender_id is not None
                and str(envelope.sender_id) == envelope.chat_id
            ):
                direction = "inbound"
        return IngressBinding(envelope, account.id, direction)

    @asynccontextmanager
    async def ingress(
        self, session: AsyncSession, command: AcceptTelegramReceiptCommand
    ) -> AsyncIterator[IngressBinding | None]:
        binding = await self.resolve_ingress(session, command)
        await session.rollback()
        if binding is None or binding.account_id is None:
            yield binding
            return
        async with dialog_guard(
            self.ingress_guards,
            workspace=self.workspace,
            account_id=binding.account_id,
            chat_id=binding.envelope.chat_id,
        ):
            fresh = await self.resolve_ingress(session, command)
            if fresh is None or fresh.account_id != binding.account_id:
                await session.rollback()
                raise sequence_error("sequence_guard_unavailable")
            yield fresh

    async def observe_connector(
        self, session: AsyncSession, command: AcceptTelegramReceiptCommand
    ) -> None:
        if command.bot_key != "telegram_business" or not isinstance(
            command.payload.get("business_connection"), dict
        ):
            return
        # Authenticated connection/rights updates invalidate continuity. A delayed
        # processing worker cannot silently retain the previous send authority.
        await session.execute(
            insert(ReplyConnectorAdmission)
            .values(
                id=uuid4(),
                workspace_ref=self.workspace,
                connector_key="telegram_business",
                state="blocked",
                safe_reason="connection_changed",
                version=1,
                observed_at=command.received_at,
            )
            .on_conflict_do_update(
                index_elements=["workspace_ref", "connector_key"],
                set_={
                    "state": "blocked",
                    "safe_reason": "connection_changed",
                    "observed_at": command.received_at,
                    "continuity_until": None,
                    "version": ReplyConnectorAdmission.version + 1,
                },
            )
        )

    async def inbound(
        self, session: AsyncSession, binding: IngressBinding | None, receipt_id: UUID, now: datetime
    ) -> None:
        if binding is None or binding.direction == "outbound":
            return
        if binding.account_id is None:
            await session.execute(
                insert(ReplyConnectorAdmission)
                .values(
                    id=uuid4(),
                    workspace_ref=self.workspace,
                    connector_key="telegram_business",
                    state="blocked",
                    safe_reason="connection_unresolved",
                    version=1,
                    observed_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["workspace_ref", "connector_key"],
                    set_={
                        "state": "blocked",
                        "safe_reason": "connection_unresolved",
                        "observed_at": now,
                        "version": ReplyConnectorAdmission.version + 1,
                    },
                )
            )
            return
        guard = await self.guard_row(session, binding.account_id, binding.envelope.chat_id)
        if binding.direction == "unresolved":
            guard.health_state = "blocked"
            guard.guard_version += 1
            reason = "guard_unavailable"
        else:
            existing = await session.scalar(
                select(ReplyInboundSignal).where(
                    ReplyInboundSignal.dialog_guard_id == guard.id,
                    ReplyInboundSignal.connector_key == "telegram_business",
                    ReplyInboundSignal.provider_message_id == binding.envelope.message_id,
                )
            )
            if existing is not None:
                if existing.payload_digest != binding.envelope.digest:
                    raise TelegramReceiptError("telegram_receipt_mutated_replay")
                return
            guard.inbound_revision += 1
            guard.last_inbound_receipt_ref = receipt_id
            guard.updated_at = now
            session.add(
                ReplyInboundSignal(
                    receipt_ref=receipt_id,
                    dialog_guard_id=guard.id,
                    connector_key="telegram_business",
                    provider_message_id=binding.envelope.message_id,
                    revision=guard.inbound_revision,
                    payload_digest=binding.envelope.digest,
                    kind="message",
                    accepted_at=now,
                )
            )
            reason = "recipient_replied"
        sequences = (
            await session.scalars(
                select(ReplySequence)
                .where(
                    ReplySequence.guard_id == guard.id,
                    ReplySequence.state == "active",
                )
                .order_by(ReplySequence.id)
                .with_for_update()
            )
        ).all()
        for sequence in sequences:
            await self.stop(
                session, sequence, reason=reason, now=now, receipt_id=receipt_id, notify=True
            )

    async def stop(
        self,
        session: AsyncSession,
        sequence: ReplySequence,
        *,
        reason: str,
        now: datetime,
        receipt_id: UUID | None = None,
        notify: bool = False,
    ) -> None:
        if sequence.state != "active":
            return
        if sequence.admitted_attempt_id is not None:
            # A previous session barrier can have died while its process is paused.
            # Admission's monotonic start window begins BEFORE its SQL commit.
            # Observing that durable admission, then waiting longer than the window,
            # fences a resumed old process at the literal HTTP adapter boundary.
            # This delay never reclassifies an already-started request as unsent.
            await asyncio.sleep(0.11)
        sequence.state = "stopped"
        sequence.stop_reason = reason
        sequence.stopped_at = now
        sequence.stop_receipt_ref = receipt_id
        sequence.version += 1
        items = (
            await session.scalars(
                select(ReplySequenceItem)
                .where(ReplySequenceItem.sequence_id == sequence.id)
                .order_by(ReplySequenceItem.position)
                .with_for_update()
            )
        ).all()
        for item in items:
            if item.state in {"scheduled", "reserved"}:
                item.state = "cancelled"
                item.safe_error_code = reason
                item.finished_at = now
        await self.project(session, items)
        session.add(
            AuditLog(
                actor="reply-sequence",
                action="reply_sequence_stopped",
                account_id=sequence.account_id,
                project_id=sequence.project_id,
                metadata_={"sequence_id": str(sequence.id), "reason": reason},
            )
        )
        if notify:
            session.add(
                ReplySequenceNotification(sequence_id=sequence.id, kind=reason, next_attempt_at=now)
            )

    async def project(self, session: AsyncSession, items: Sequence[ReplySequenceItem]) -> None:
        for item in items:
            draft = await session.get(ReplyDraft, item.draft_id)
            if draft is None:
                continue
            context = dict(draft.prompt_context or {})
            prior = context.get("reply_sequence")
            if isinstance(prior, dict):
                context["reply_sequence"] = {
                    **prior,
                    "state": (
                        item.state
                        if item.state in {"sent", "failed", "unknown", "cancelled"}
                        else "managed_v2"
                    ),
                }
                draft.prompt_context = context

    def resource_keys(self, *, account: UUID, chat: str) -> list[tuple[str, float]]:
        return sorted(
            [
                ("account:" + str(account), 1.0),
                ("bot:" + self.bot_resource, 0.25),
                ("recipient:" + text_digest(self.workspace + ":" + str(account) + ":" + chat), 1.0),
            ]
        )

    async def budgets(
        self, session: AsyncSession, *, account: UUID, chat: str, now: datetime
    ) -> list[tuple[ReplyTransportBudget, float]]:
        result = []
        for key, gap in self.resource_keys(account=account, chat=chat):
            await session.execute(
                insert(ReplyTransportBudget)
                .values(
                    resource_key=key,
                    next_allowed_at=now,
                    blocked_indefinitely=False,
                )
                .on_conflict_do_nothing()
            )
            row = await session.scalar(
                select(ReplyTransportBudget)
                .where(ReplyTransportBudget.resource_key == key)
                .with_for_update()
            )
            assert row is not None
            result.append((row, gap))
        return result

    @staticmethod
    def budget_ready(budgets: list[tuple[ReplyTransportBudget, float]], now: datetime) -> bool:
        return all(
            not b.blocked_indefinitely
            and b.next_allowed_at <= now
            and (b.blocked_until is None or b.blocked_until <= now)
            for b, _ in budgets
        )

    async def candidates(self, *, limit: int = 100) -> list[UUID]:
        async with self.sessions() as session:
            now = await self.db_now(session)
            query = select(ReplySequence.id, ReplySequence.next_due_at).where(
                ReplySequence.workspace_ref == self.workspace,
                ReplySequence.state == "active",
                ReplySequence.next_due_at <= now,
            )
            if self.cursor is not None:
                query = query.where(
                    tuple_(ReplySequence.next_due_at, ReplySequence.id) > self.cursor
                )
            rows = (
                await session.execute(
                    query.order_by(ReplySequence.next_due_at, ReplySequence.id).limit(
                        min(limit, 100)
                    )
                )
            ).all()
            self.cursor = (rows[-1][1], rows[-1][0]) if len(rows) == min(limit, 100) else None
            return [row[0] for row in rows]

    async def claim(self, sequence_id: UUID) -> DispatchClaim | None:
        async with self.sessions() as session:
            try:
                now = await self.db_now(session)
                control = await self.control(session)
                await self.healthy(session, now)
                sequence = await session.scalar(
                    select(ReplySequence)
                    .where(
                        ReplySequence.id == sequence_id,
                        ReplySequence.workspace_ref == self.workspace,
                        ReplySequence.state == "active",
                        ReplySequence.next_due_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
                if sequence is None or sequence.admitted_attempt_id is not None:
                    return None
                from app.infrastructure.reply_sequence_operations import canary_sequence_allowed

                if not await canary_sequence_allowed(session, self, sequence, now):
                    return None
                guard = await session.get(ReplyDialogGuard, sequence.guard_id)
                assert guard is not None
                if (
                    guard.health_state != "healthy"
                    or guard.inbound_revision != sequence.queued_revision
                ):
                    return None
                if sequence.claim_expires_at is not None and sequence.claim_expires_at > now:
                    return None
                items = (
                    await session.scalars(
                        select(ReplySequenceItem)
                        .where(ReplySequenceItem.sequence_id == sequence.id)
                        .order_by(ReplySequenceItem.position)
                    )
                ).all()
                if (
                    len(items) != sequence.item_count
                    or [i.position for i in items] != list(range(len(items)))
                    or sequence.next_position >= len(items)
                ):
                    return None  # guard-owning reconciler records integrity stop
                item = items[sequence.next_position]
                if item.state not in {"scheduled", "reserved"} or item.scheduled_at > now:
                    return None
                if item.position:
                    previous = items[item.position - 1]
                    if previous.state != "sent" or previous.finished_at is None:
                        return None
                    if previous.finished_at + timedelta(seconds=60) > now:
                        return None
                budgets = await self.budgets(
                    session, account=sequence.account_id, chat=guard.provider_chat_id, now=now
                )
                if not self.budget_ready(budgets, now):
                    return None
                # Reclaim only unadmitted preflight ownership, never a sending attempt.
                stale = (
                    await session.scalars(
                        select(ReplyDispatchPermit)
                        .where(
                            ReplyDispatchPermit.workspace_ref == self.workspace,
                            ReplyDispatchPermit.sequence_id == sequence.id,
                        )
                        .with_for_update()
                    )
                ).all()
                for stale_slot in stale:
                    if stale_slot.state != "preflight" or stale_slot.attempt_id is not None:
                        return None
                    self.free_permit(stale_slot)
                await session.flush()
                used = (
                    await session.scalars(
                        select(ReplyDispatchPermit).where(
                            ReplyDispatchPermit.workspace_ref == self.workspace,
                            ReplyDispatchPermit.state != "free",
                        )
                    )
                ).all()
                if (
                    sum(p.account_id == sequence.account_id for p in used) >= control.account_cap
                    or sum(p.bot_resource_id == self.bot_resource for p in used) >= control.bot_cap
                ):
                    return None
                cap = control.max_inflight if control.mode == "parallel" else 1
                slot = await session.scalar(
                    select(ReplyDispatchPermit)
                    .where(
                        ReplyDispatchPermit.workspace_ref == self.workspace,
                        ReplyDispatchPermit.slot < cap,
                        ReplyDispatchPermit.state == "free",
                    )
                    .order_by(ReplyDispatchPermit.slot)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if slot is None:
                    return None
                token = uuid4()
                sequence.claim_token = token
                sequence.claim_owner = self.owner
                sequence.claim_expires_at = now + timedelta(seconds=60)
                sequence.dispatch_generation = control.generation
                item.state = "reserved"
                slot.state = "preflight"
                slot.sequence_id = sequence.id
                slot.account_id = sequence.account_id
                slot.bot_resource_id = self.bot_resource
                slot.token = token
                slot.owner = self.owner
                slot.expires_at = sequence.claim_expires_at
                claim = DispatchClaim(
                    sequence.id,
                    token,
                    item.draft_id,
                    sequence.account_id,
                    sequence.project_id,
                    sequence.task_id,
                    sequence.conversation_id,
                    self.workspace,
                    guard.provider_chat_id,
                    sequence.account_label,
                    sequence.principal_subject,
                    item.position,
                    control.generation,
                    sequence.canary_execution_id,
                )
                await session.commit()
                return claim
            except ReplySequenceError:
                await session.rollback()
                return None

    @staticmethod
    def free_permit(slot: ReplyDispatchPermit) -> None:
        slot.state = "free"
        slot.sequence_id = slot.account_id = slot.token = slot.attempt_id = None
        slot.bot_resource_id = slot.owner = None
        slot.expires_at = None

    async def admission(
        self, session: AsyncSession, claim: DispatchClaim, attempt: SendAttempt, draft_text: str
    ) -> None:
        now = await self.db_now(session)
        control = await self.control(session)
        await self.healthy(session, now)
        sequence = await session.scalar(
            select(ReplySequence).where(ReplySequence.id == claim.sequence_id).with_for_update()
        )
        if (
            sequence is None
            or sequence.state != "active"
            or sequence.claim_token != claim.token
            or sequence.claim_expires_at is None
            or sequence.claim_expires_at <= now
            or sequence.dispatch_generation != control.generation
            or claim.generation != control.generation
        ):
            raise sequence_error("sequence_stopped")
        from app.infrastructure.reply_sequence_operations import canary_sequence_allowed

        if not await canary_sequence_allowed(session, self, sequence, now):
            raise sequence_error("canary_admission_closed")
        guard = await session.get(ReplyDialogGuard, sequence.guard_id)
        if (
            guard is None
            or guard.health_state != "healthy"
            or guard.inbound_revision != sequence.queued_revision
        ):
            raise sequence_error("recipient_context_changed")
        context_state = await approval_context_state(
            session,
            AccessScope(
                workspace_id=claim.workspace,
                account_ids=frozenset({claim.account_id}),
                project_ids=frozenset({claim.project_id}),
            ),
            claim.task_id,
        )
        if context_state is None or context_state.revision != sequence.queued_revision:
            raise sequence_error("recipient_context_changed")
        if context_state.blocked_reason:
            raise sequence_error(context_state.blocked_reason)
        if (
            sequence.account_id,
            sequence.project_id,
            sequence.conversation_id,
            sequence.task_id,
            sequence.workspace_ref,
            sequence.principal_subject,
            guard.provider_chat_id,
        ) != (
            claim.account_id,
            claim.project_id,
            claim.conversation_id,
            claim.task_id,
            claim.workspace,
            claim.principal_subject,
            claim.recipient,
        ):
            raise sequence_error("sequence_binding_mismatch")
        items = (
            await session.scalars(
                select(ReplySequenceItem)
                .where(ReplySequenceItem.sequence_id == sequence.id)
                .order_by(ReplySequenceItem.position)
                .with_for_update()
            )
        ).all()
        if (
            len(items) != sequence.item_count
            or [i.position for i in items] != list(range(len(items)))
            or sequence.next_position != claim.position
            or claim.position >= len(items)
        ):
            raise sequence_error("sequence_integrity_invalid")
        item = items[claim.position]
        if item.draft_id != claim.draft_id or item.state != "reserved":
            raise sequence_error("sequence_stopped")
        if item.approved_text_sha256 != text_digest(draft_text):
            raise sequence_error("approval_changed")
        drafts = (
            await session.scalars(
                select(ReplyDraft).where(ReplyDraft.id.in_([i.draft_id for i in items]))
            )
        ).all()
        approved_hashes = {i.draft_id: i.approved_text_sha256 for i in items}
        if len(drafts) != len(items) or any(
            text_digest(d.draft_text) != approved_hashes[d.id]
            or (d.account_id, d.project_id, d.reply_task_id, d.conversation_id)
            != (
                sequence.account_id,
                sequence.project_id,
                sequence.task_id,
                sequence.conversation_id,
            )
            for d in drafts
        ):
            raise sequence_error("approval_changed")
        current_digest = approval_digest(
            workspace=sequence.workspace_ref,
            account=sequence.account_id,
            project=sequence.project_id,
            task=sequence.task_id,
            conversation=sequence.conversation_id,
            recipient=guard.provider_chat_id,
            revision=sequence.queued_revision,
            drafts=[(i.draft_id, i.approved_text_sha256) for i in items],
        )
        if current_digest != sequence.approval_digest:
            raise sequence_error("approval_changed")
        due = max(item.scheduled_at, sequence.next_due_at)
        if item.position:
            previous = items[item.position - 1]
            if previous.state != "sent" or previous.finished_at is None:
                raise sequence_error("sequence_integrity_invalid")
            due = max(due, previous.finished_at + timedelta(seconds=60))
        if now < due:
            raise sequence_error("sequence_not_due")
        budgets = await self.budgets(
            session, account=sequence.account_id, chat=guard.provider_chat_id, now=now
        )
        if not self.budget_ready(budgets, now):
            raise sequence_error("sequence_rate_wait")
        slot = await session.scalar(
            select(ReplyDispatchPermit)
            .where(
                ReplyDispatchPermit.workspace_ref == self.workspace,
                ReplyDispatchPermit.sequence_id == sequence.id,
            )
            .with_for_update()
        )
        if (
            slot is None
            or slot.token != claim.token
            or slot.state != "preflight"
            or slot.expires_at is None
            or slot.expires_at <= now
        ):
            raise sequence_error("sequence_claim_stale")
        cap = control.max_inflight if control.mode == "parallel" else 1
        if slot.slot >= cap:
            raise sequence_error("sequence_config_mismatch")
        # All resource/permit locks are held now. Use a fresh DB timestamp so
        # lock contention cannot turn a reservation into an already-expired gap.
        now = await self.db_now(session)
        await self.healthy(session, now)
        if sequence.claim_expires_at <= now or slot.expires_at <= now:
            raise sequence_error("sequence_claim_stale")
        for budget, gap in budgets:
            budget.next_allowed_at = now + timedelta(seconds=gap + 0.1)
        item.state = "sending"
        item.attempt_id = attempt.id
        sequence.admitted_attempt_id = attempt.id
        sequence.last_admitted_at = now
        slot.state = "dispatching"
        slot.attempt_id = attempt.id

    async def project_outcome(self, claim: DispatchClaim, *, coroutine_finished: bool) -> None:
        async with self.sessions() as session:
            # Existing-attempt mutation order is attempt -> sequence -> items -> slot.
            attempt = await session.scalar(
                select(SendAttempt)
                .where(SendAttempt.reply_draft_id == claim.draft_id)
                .with_for_update()
            )
            sequence = await session.scalar(
                select(ReplySequence).where(ReplySequence.id == claim.sequence_id).with_for_update()
            )
            if sequence is None or sequence.claim_token != claim.token:
                return
            item = await session.get(ReplySequenceItem, (sequence.id, claim.position))
            if item is None:
                return
            now = await self.db_now(session)
            if attempt is not None and attempt.status in {"sent", "failed", "unknown", "cancelled"}:
                item.state = attempt.status
                item.finished_at = attempt.finished_at
                item.attempt_id = attempt.id
                item.safe_error_code = attempt.error_code
                if sequence.state == "active":
                    if attempt.status == "sent" and attempt.finished_at is not None:
                        sequence.next_position = max(sequence.next_position, item.position + 1)
                        sequence.next_due_at = attempt.finished_at + timedelta(seconds=60)
                        if sequence.next_position == sequence.item_count:
                            sequence.state = "completed"
                    else:
                        await self.stop(
                            session,
                            sequence,
                            now=now,
                            reason=(
                                "send_unknown" if attempt.status == "unknown" else "send_failed"
                            ),
                        )
                sequence.version += 1
                await self.project(session, [item])
            slot = await session.scalar(
                select(ReplyDispatchPermit)
                .where(
                    ReplyDispatchPermit.workspace_ref == self.workspace,
                    ReplyDispatchPermit.sequence_id == sequence.id,
                )
                .with_for_update()
            )
            terminal = attempt is not None and attempt.status in {
                "sent",
                "failed",
                "unknown",
                "cancelled",
            }
            if slot is not None and slot.token == claim.token:
                if coroutine_finished and terminal:
                    self.free_permit(slot)
                elif not coroutine_finished:
                    slot.state = "quarantined"
            if coroutine_finished and terminal:
                sequence.admitted_attempt_id = None
                sequence.claim_token = sequence.claim_expires_at = None
                sequence.claim_owner = None
            await session.commit()

    async def release_preflight(self, claim: DispatchClaim, *, error: str | None = None) -> None:
        async with self.sessions() as session:
            sequence = await session.scalar(
                select(ReplySequence).where(ReplySequence.id == claim.sequence_id).with_for_update()
            )
            if sequence is None or sequence.claim_token != claim.token:
                return
            if sequence.admitted_attempt_id is not None:
                return
            if error is not None:
                await self.stop(session, sequence, reason=error, now=await self.db_now(session))
            item = await session.get(ReplySequenceItem, (sequence.id, claim.position))
            if item is not None and item.state == "reserved":
                item.state = "scheduled"
            slot = await session.scalar(
                select(ReplyDispatchPermit)
                .where(
                    ReplyDispatchPermit.workspace_ref == self.workspace,
                    ReplyDispatchPermit.sequence_id == sequence.id,
                )
                .with_for_update()
            )
            if slot is not None and slot.token == claim.token and slot.state == "preflight":
                self.free_permit(slot)
            sequence.claim_token = sequence.claim_expires_at = None
            sequence.claim_owner = None
            await session.commit()

    async def quarantine_shutdown(self, sequence_ids: tuple[UUID, ...]) -> None:
        async with self.sessions() as session:
            control = await session.get(ReplyDispatchControl, self.workspace, with_for_update=True)
            if control is not None:
                control.mode = "disabled"
                control.generation += 1
            permits = (
                await session.scalars(
                    select(ReplyDispatchPermit)
                    .where(
                        ReplyDispatchPermit.workspace_ref == self.workspace,
                        ReplyDispatchPermit.sequence_id.in_(sequence_ids),
                        ReplyDispatchPermit.owner == self.owner,
                        ReplyDispatchPermit.state != "free",
                    )
                    .with_for_update()
                )
            ).all()
            for permit in permits:
                permit.state = "quarantined"
            await session.commit()

    async def apply_cooldown(self, claim: DispatchClaim, retry_after: int | None) -> None:
        async with self.sessions() as session:
            now = await self.db_now(session)
            budgets = await self.budgets(
                session, account=claim.account_id, chat=claim.recipient, now=now
            )
            for row, _ in budgets:
                result = extend_reply_sequence_cooldown(
                    now=now,
                    retry_after_seconds=retry_after,
                    current=ReplySequenceCooldown(row.blocked_until, row.blocked_indefinitely),
                )
                row.blocked_until = result.blocked_until
                row.blocked_indefinitely = result.blocked_indefinitely
            await session.commit()

    async def health_reason(self, *, closed_dispatch_ok: bool = False) -> str | None:
        async with self.sessions() as session:
            if closed_dispatch_ok:
                observed = await session.get(ReplyDispatchControl, self.workspace)
                if (
                    observed is not None and observed.migration_complete
                    and observed.mode == "disabled"
                ):
                    return None  # Completed idle dispatcher loop; this is not send admission.
            try:
                control = await self.control(session)
                await self.healthy(session, await self.db_now(session))
            except ReplySequenceError as exc:
                return exc.code
            quarantined = await session.scalar(
                select(func.count())
                .select_from(ReplyDispatchPermit)
                .where(
                    ReplyDispatchPermit.workspace_ref == self.workspace,
                    ReplyDispatchPermit.state == "quarantined",
                )
            )
            cap = control.max_inflight if control.mode == "parallel" else 1
            if quarantined is not None and quarantined >= cap:
                return "sequence_permits_quarantined"
        return None

    async def reconcile(self, *, limit: int = 100) -> int:
        """Repair projections from the ledger; never repeat an external send."""
        from app.infrastructure.reply_sequence_guard import SequenceGuardUnavailable

        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        ReplySequence.id,
                        ReplySequence.account_id,
                        ReplyDialogGuard.provider_chat_id,
                    )
                    .join(ReplyDialogGuard, ReplySequence.guard_id == ReplyDialogGuard.id)
                    .where(
                        ReplySequence.workspace_ref == self.workspace,
                        or_(
                            ReplySequence.state == "active", ReplySequence.claim_token.is_not(None)
                        ),
                    )
                    .where(
                        ReplySequence.id > self.recovery_cursor if self.recovery_cursor else true()
                    )
                    .order_by(ReplySequence.id)
                    .limit(min(limit, 100))
                )
            ).all()
        self.recovery_cursor = rows[-1][0] if len(rows) == min(limit, 100) else None
        repaired = 0
        for sequence_id, account_id, chat_id in rows:
            try:
                async with dialog_guard(
                    self.ingress_guards,
                    workspace=self.workspace,
                    account_id=account_id,
                    chat_id=chat_id,
                    wait=False,
                ):
                    async with self.sessions() as session:
                        # Read first, then lock any existing attempts before the header.
                        ids = list(
                            (
                                await session.scalars(
                                    select(ReplySequenceItem.attempt_id).where(
                                        ReplySequenceItem.sequence_id == sequence_id,
                                        ReplySequenceItem.attempt_id.is_not(None),
                                    )
                                )
                            ).all()
                        )
                        attempts = {
                            a.id: a
                            for a in (
                                await session.scalars(
                                    select(SendAttempt)
                                    .where(SendAttempt.id.in_(ids))
                                    .order_by(SendAttempt.id)
                                    .with_for_update()
                                )
                            ).all()
                        }
                        sequence = await session.scalar(
                            select(ReplySequence)
                            .where(ReplySequence.id == sequence_id)
                            .with_for_update()
                        )
                        assert sequence is not None
                        items = list(
                            (
                                await session.scalars(
                                    select(ReplySequenceItem)
                                    .where(ReplySequenceItem.sequence_id == sequence_id)
                                    .order_by(ReplySequenceItem.position)
                                    .with_for_update()
                                )
                            ).all()
                        )
                        now = await self.db_now(session)
                        invalid = len(items) != sequence.item_count or [
                            i.position for i in items
                        ] != list(range(len(items)))
                        for item in items:
                            attempt = attempts.get(item.attempt_id) if item.attempt_id else None
                            if attempt is None:
                                if item.attempt_id is not None or item.state in {"sending", "sent"}:
                                    invalid = True
                                continue
                            if attempt.reply_draft_id != item.draft_id:
                                invalid = True
                                continue
                            if (
                                attempt.status == "sending"
                                and attempt.started_at is not None
                                and attempt.started_at + timedelta(seconds=30) < now
                            ):
                                attempt.status = "unknown"
                                attempt.error_code = "sequence_recovery_unknown"
                                attempt.finished_at = now
                            if attempt.status in {"sent", "failed", "unknown", "cancelled"}:
                                item.state = attempt.status
                                item.finished_at = attempt.finished_at
                                item.safe_error_code = attempt.error_code
                        if invalid:
                            await self.stop(
                                session, sequence, reason="sequence_integrity_invalid", now=now
                            )
                        elif any(i.state in {"failed", "unknown", "cancelled"} for i in items):
                            await self.stop(
                                session,
                                sequence,
                                reason=(
                                    "send_unknown"
                                    if any(i.state == "unknown" for i in items)
                                    else "send_failed"
                                ),
                                now=now,
                            )
                        elif sequence.state == "active":
                            position = 0
                            while position < len(items) and items[position].state == "sent":
                                if items[position].finished_at is None:
                                    invalid = True
                                    break
                                position += 1
                            if invalid:
                                await self.stop(
                                    session, sequence, reason="sequence_integrity_invalid", now=now
                                )
                            else:
                                sequence.next_position = position
                                if position == len(items):
                                    sequence.state = "completed"
                                elif position:
                                    finished = items[position - 1].finished_at
                                    assert finished is not None
                                    sequence.next_due_at = finished + timedelta(seconds=60)
                        slot = await session.scalar(
                            select(ReplyDispatchPermit)
                            .where(
                                ReplyDispatchPermit.workspace_ref == self.workspace,
                                ReplyDispatchPermit.sequence_id == sequence.id,
                            )
                            .with_for_update()
                        )
                        if slot is not None:
                            attempt = attempts.get(slot.attempt_id) if slot.attempt_id else None
                            safe_terminal = attempt is not None and attempt.status in {
                                "sent",
                                "failed",
                                "cancelled",
                            }
                            expired_preflight = (
                                slot.state == "preflight"
                                and slot.attempt_id is None
                                and slot.expires_at is not None
                                and slot.expires_at <= now
                            )
                            if safe_terminal or expired_preflight:
                                self.free_permit(slot)
                                sequence.claim_token = sequence.claim_expires_at = None
                                sequence.claim_owner = None
                                sequence.admitted_attempt_id = None
                                for item in items:
                                    if item.state == "reserved" and item.attempt_id is None:
                                        item.state = (
                                            "scheduled"
                                            if sequence.state == "active"
                                            else "cancelled"
                                        )
                            elif attempt is not None and attempt.status == "unknown":
                                slot.state = "quarantined"
                        await self.project(session, items)
                        await session.commit()
                        repaired += 1
            except SequenceGuardUnavailable:
                continue
        return repaired

    async def detail(
        self,
        session: AsyncSession,
        principal: Principal,
        sequence_id: UUID,
        *,
        cursor: int = 0,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        scope = principal.access_scope
        # One bounded SQL statement gives header, items, revision and notification
        # from one PostgreSQL snapshot, even if a stop/finalization commits now.
        rows = (
            await session.execute(
                select(
                    ReplySequence,
                    ReplyDialogGuard,
                    ReplySequenceItem,
                    ReplySequenceNotification,
                    ReplyDraft.draft_text,
                )
                .join(ReplyDialogGuard, ReplyDialogGuard.id == ReplySequence.guard_id)
                .outerjoin(ReplySequenceItem, ReplySequenceItem.sequence_id == ReplySequence.id)
                .outerjoin(
                    ReplyDraft,
                    and_(
                        ReplyDraft.id == ReplySequenceItem.draft_id,
                        ReplyDraft.account_id == ReplySequence.account_id,
                        ReplyDraft.project_id == ReplySequence.project_id,
                        ReplyDraft.conversation_id == ReplySequence.conversation_id,
                        ReplyDraft.reply_task_id == ReplySequence.task_id,
                        ReplyDraft.account_id.in_(scope.account_ids),
                        ReplyDraft.project_id.in_(scope.project_ids),
                    ),
                )
                .outerjoin(
                    ReplySequenceNotification,
                    ReplySequenceNotification.sequence_id == ReplySequence.id,
                )
                .where(
                    ReplySequence.id == sequence_id,
                    ReplySequence.workspace_ref == scope.workspace_id,
                    ReplySequence.account_id.in_(scope.account_ids),
                    ReplySequence.project_id.in_(scope.project_ids),
                )
                .order_by(ReplySequenceItem.position)
            )
        ).all()
        if not rows:
            return None
        sequence, guard, _, notification, _ = rows[0]
        draft_texts = {r[2].draft_id: r[4] for r in rows if r[2] is not None}
        all_items = [r[2] for r in rows if r[2] is not None]
        counts = {
            state: sum(i.state == state for i in all_items)
            for state in (
                "scheduled",
                "reserved",
                "sending",
                "sent",
                "failed",
                "unknown",
                "cancelled",
            )
        }
        items = [i for i in all_items if i.position >= cursor][:limit]
        now = await self.db_now(session)
        budgets = (
            await session.scalars(
                select(ReplyTransportBudget).where(
                    ReplyTransportBudget.resource_key.in_(
                        [
                            k
                            for k, _ in self.resource_keys(
                                account=sequence.account_id, chat=guard.provider_chat_id
                            )
                        ]
                    )
                )
            )
        ).all()
        wait_until = max(
            [b.next_allowed_at for b in budgets]
            + [b.blocked_until for b in budgets if b.blocked_until is not None],
            default=now,
        )
        next_position = items[-1].position + 1 if items else sequence.item_count
        return {
            "sequence_id": str(sequence.id),
            "task_id": str(sequence.task_id),
            "state": sequence.state,
            "stop_reason": sequence.stop_reason,
            "stopped_at": sequence.stopped_at.isoformat() if sequence.stopped_at else None,
            "stopped_by_receipt_ref": str(sequence.stop_receipt_ref)
            if sequence.stop_receipt_ref
            else None,
            "account_label": sequence.account_label,
            "recipient_display": await recipient_display(
                session,
                account_id=sequence.account_id,
                project_id=sequence.project_id,
                conversation_id=sequence.conversation_id,
                chat_id=guard.provider_chat_id,
            ),
            "total": sequence.item_count,
            "counts": {
                state: counts.get(state, 0)
                for state in (
                    "scheduled",
                    "reserved",
                    "sending",
                    "sent",
                    "failed",
                    "unknown",
                    "cancelled",
                )
            },
            "items": [
                {
                    "position": i.position,
                    "draft_id": str(i.draft_id),
                    "draft_text": draft_texts[i.draft_id],
                    "draft_text_matches_approval": (
                        text_digest(draft_texts[i.draft_id]) == i.approved_text_sha256
                        if draft_texts[i.draft_id] is not None
                        else None
                    ),
                    "item_state": i.state,
                    "safe_error_code": i.safe_error_code,
                    "confirmed_at": i.finished_at.isoformat() if i.finished_at else None,
                }
                for i in items
            ],
            "notification": {
                "state": notification.delivery_state if notification else "not_required",
                "safe_error_code": notification.safe_error_code if notification else None,
                "delivered_at": (
                    notification.finished_at.isoformat()
                    if notification
                    and notification.delivery_state == "sent"
                    and notification.finished_at
                    else None
                ),
            },
            "can_auto_resume": False,
            "requires_new_approval": sequence.state == "stopped",
            "queued_revision": sequence.queued_revision,
            "dialog_inbound_revision": guard.inbound_revision,
            "version": sequence.version,
            "observed_at": now.isoformat(),
            "next_position": sequence.next_position,
            "next_due_at": sequence.next_due_at.isoformat(),
            "admitted": sequence.admitted_attempt_id is not None,
            "rate_wait_until": wait_until.isoformat() if wait_until > now else None,
            "resource_blocked": any(b.blocked_indefinitely for b in budgets),
            "next_cursor": str(next_position) if next_position < sequence.item_count else None,
        }

    async def summaries(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        account_id: UUID | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        scope = principal.access_scope
        filters = [
            ReplySequence.workspace_ref == scope.workspace_id,
            ReplySequence.account_id.in_(scope.account_ids),
            ReplySequence.project_id.in_(scope.project_ids),
        ]
        if account_id is not None:
            filters.append(ReplySequence.account_id == account_id)
        if chat_id is not None:
            filters.append(ReplyDialogGuard.provider_chat_id == chat_id)
        scoped = select(ReplySequence.id).join(ReplyDialogGuard).where(*filters)
        state_counts: dict[str, int] = {
            state: count
            for state, count in (
                await session.execute(
                    select(ReplySequence.state, func.count())
                    .where(ReplySequence.id.in_(scoped))
                    .group_by(ReplySequence.state)
                )
            ).all()
        }
        notification_counts: dict[str, int] = {
            state: count
            for state, count in (
                await session.execute(
                    select(ReplySequenceNotification.delivery_state, func.count())
                    .where(ReplySequenceNotification.sequence_id.in_(scoped))
                    .group_by(ReplySequenceNotification.delivery_state)
                )
            ).all()
        }
        rows = (
            await session.execute(
                select(ReplySequence, ReplySequenceNotification.delivery_state)
                .outerjoin(
                    ReplySequenceNotification,
                    ReplySequenceNotification.sequence_id == ReplySequence.id,
                )
                .where(ReplySequence.id.in_(scoped))
                .order_by(ReplySequence.created_at.desc(), ReplySequence.id)
                .limit(20)
            )
        ).all()
        return {
            "items": [
                {
                    "sequence_id": str(s.id),
                    "task_id": str(s.task_id),
                    "state": s.state,
                    "stop_reason": s.stop_reason,
                    "total": s.item_count,
                    "notification_state": notification or "not_required",
                }
                for s, notification in rows
            ],
            "limit": 20,
            "counts": {
                state: state_counts.get(state, 0) for state in ("active", "stopped", "completed")
            },
            "notification_counts": {
                state: notification_counts.get(state, 0)
                for state in ("pending", "sending", "sent", "failed", "unknown", "blocked_route")
            },
        }
