"""SQLAlchemy and Telegram adapter for explicit idempotent reply delivery."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.application.account_labels import account_label
from app.application.reply_sends import (
    ReplySendError,
    ReplySendResult,
    SendApprovedReplyCommand,
)
from app.core.config import Settings
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    SendAttemptStatus,
    TelegramBusinessConnection,
)
from app.services.telegram import TelegramApiError, TelegramClient

_SENDABLE_DRAFT_STATUSES = frozenset(
    {
        ReplyDraftStatus.GENERATED.value,
        ReplyDraftStatus.EDITED.value,
    }
)
_ACTIVE_CONVERSATION_ATTEMPT_STATUSES = (
    SendAttemptStatus.RESERVED.value,
    SendAttemptStatus.SENDING.value,
    SendAttemptStatus.UNKNOWN.value,
)


@dataclass(frozen=True, slots=True)
class _SendContextSnapshot:
    """Immutable send intent copied before any provider I/O."""

    draft_id: UUID
    draft_text: str
    draft_status: str
    draft_updated_at: datetime
    task_id: UUID
    task_status: str
    conversation_id: UUID
    conversation_business_connection_id: str | None
    conversation_last_inbound_at: datetime | None
    trigger_message_id: UUID
    trigger_business_connection_id: str | None
    target_chat_id: str
    account_id: UUID
    project_id: UUID
    account_alias: str
    account_label: str
    account_is_active: bool
    account_send_enabled: bool
    bot_id: UUID
    person_id: UUID | None
    account_telegram_user_id: int | None
    counterparty_display_name: str | None

    @property
    def origin_connection_id(self) -> str | None:
        return self.conversation_business_connection_id or self.trigger_business_connection_id


@dataclass(frozen=True, slots=True)
class _ConnectionCandidate:
    id: UUID
    account_id: UUID
    business_connection_id: str
    telegram_user_id: int | None
    user_chat_id: int | None
    rights_fingerprint: str
    is_enabled: bool
    received_at: datetime
    last_update_at: datetime
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class _SendIntent:
    context: _SendContextSnapshot
    candidates: tuple[_ConnectionCandidate, ...]
    covered_task_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class _ConnectionProbe:
    connection_id: UUID
    business_connection_id: str
    rights: tuple[tuple[str, bool], ...]

    def rights_dict(self) -> dict[str, bool]:
        return dict(self.rights)


@dataclass(frozen=True, slots=True)
class _AttemptSnapshot:
    id: UUID
    draft_id: UUID
    task_id: UUID
    account_id: UUID
    project_id: UUID
    account_label: str
    target_chat_id: str
    status: str
    provider_message_id: str | None
    error_code: str | None
    error_detail: str | None


@dataclass(frozen=True, slots=True)
class _Reservation:
    context: _SendContextSnapshot
    attempt: _AttemptSnapshot
    business_connection_id: str
    covered_task_ids: tuple[UUID, ...]


class SqlTelegramReplySender:
    """Reserve before Telegram, then persist a bounded delivery outcome."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        telegram: TelegramClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._telegram = telegram
        self._settings = settings

    async def send(self, command: SendApprovedReplyCommand) -> ReplySendResult:
        preflight = await self._preflight(command)
        if isinstance(preflight, ReplySendResult):
            return preflight

        probe = await self._probe_connection(preflight)
        reservation = await self._reserve(
            command=command,
            intent=preflight,
            probe=probe,
        )
        if isinstance(reservation, ReplySendResult):
            return reservation

        self._require_provider_outside_transaction()
        try:
            telegram_result = await self._telegram.send_message(
                chat_id=reservation.context.target_chat_id,
                text=reservation.context.draft_text,
                business_connection_id=reservation.business_connection_id,
            )
            if telegram_result.get("ok") is not True:
                raise RuntimeError("Telegram API did not confirm delivery")
            if _provider_message_id(telegram_result) is None:
                raise RuntimeError("Telegram API did not return a confirmed message id")
        except Exception as exc:
            status, error_code = _delivery_failure(exc)
            return await self._finalize_failure(
                command=command,
                reservation=reservation,
                status=status,
                error_code=error_code,
                error_detail=_safe_error_detail(exc),
            )

        return await self._finalize_success(
            command=command,
            reservation=reservation,
            telegram_result=telegram_result,
        )

    async def _preflight(
        self,
        command: SendApprovedReplyCommand,
    ) -> _SendIntent | ReplySendResult:
        """Read and copy send intent, then end the implicit read transaction."""

        intent: _SendIntent | None = None
        replay: ReplySendResult | None = None
        try:
            context = await self._load_context_snapshot(command, for_update=False)
            self._validate_confirmation(
                command=command,
                account_label_value=context.account_label,
                target_chat_id=context.target_chat_id,
            )
            existing = await self._load_attempt_model(draft_id=context.draft_id)
            if existing is not None:
                self._validate_attempt_binding(existing, context)
                replay = self._attempt_result(
                    attempt=_snapshot_attempt(existing),
                    context=context,
                    idempotent_replay=True,
                )
            else:
                await self._raise_if_task_send_blocked(
                    conversation_id=context.conversation_id,
                    task_id=context.task_id,
                    draft_id=context.draft_id,
                )
                self._validate_context_delivery(context)
                origin_id = context.origin_connection_id
                if not origin_id:
                    raise ReplySendError(
                        code="origin_connection_missing",
                        message=("The originating Telegram Business connection is missing"),
                    )
                candidates = await self._load_connection_candidates(
                    command=command,
                    account_id=context.account_id,
                    origin_id=origin_id,
                    for_update=False,
                )
                covered_task_ids = await self._load_covered_task_ids(
                    conversation_id=context.conversation_id,
                    account_id=context.account_id,
                    project_id=context.project_id,
                    for_update=False,
                )
                intent = _SendIntent(
                    context=context,
                    candidates=candidates,
                    covered_task_ids=covered_task_ids,
                )
        finally:
            if self._session.in_transaction():
                await self._session.rollback()

        if replay is not None:
            return replay
        if intent is None:
            raise RuntimeError("Reply send preflight did not produce an intent")
        return intent

    async def _probe_connection(self, intent: _SendIntent) -> _ConnectionProbe:
        """Validate Telegram connection rights without any database transaction."""

        validation_unavailable = False
        for candidate in intent.candidates:
            self._require_provider_outside_transaction()
            try:
                live_response = await self._telegram.get_business_connection(
                    business_connection_id=candidate.business_connection_id
                )
            except TelegramApiError as exc:
                if "BUSINESS_CONNECTION_INVALID" not in exc.description.upper():
                    validation_unavailable = True
                continue
            except (httpx.HTTPError, RuntimeError):
                validation_unavailable = True
                continue

            if not isinstance(live_response, dict):
                validation_unavailable = True
                continue
            live = live_response.get("result")
            if live_response.get("ok") is not True or not isinstance(live, dict):
                validation_unavailable = True
                continue
            if live.get("id") != candidate.business_connection_id:
                validation_unavailable = True
                continue
            if live.get("is_enabled") is not True:
                continue
            raw_rights = live.get("rights")
            if not isinstance(raw_rights, dict):
                validation_unavailable = True
                continue
            rights = raw_rights
            if rights.get("can_reply") is not True:
                continue
            frozen_rights = tuple(
                sorted(
                    (key, value)
                    for key, value in rights.items()
                    if isinstance(key, str) and isinstance(value, bool)
                )
            )
            return _ConnectionProbe(
                connection_id=candidate.id,
                business_connection_id=candidate.business_connection_id,
                rights=frozen_rights,
            )

        if validation_unavailable:
            raise ReplySendError(
                code="business_connection_validation_unavailable",
                message=("Telegram Business connection validation is temporarily unavailable"),
            )
        raise ReplySendError(
            code="business_connection_unavailable",
            message=(
                "No live Telegram Business connection with reply permission is "
                "available for the confirmed account"
            ),
        )

    async def _reserve(
        self,
        *,
        command: SendApprovedReplyCommand,
        intent: _SendIntent,
        probe: _ConnectionProbe,
    ) -> _Reservation | ReplySendResult:
        """Recheck under short locks and durably reserve before sending."""

        reservation: _Reservation | None = None
        replay: ReplySendResult | None = None
        try:
            async with self._session.begin():
                await self._lock_reservation_conversation(
                    command=command,
                    conversation_id=intent.context.conversation_id,
                )
                existing = await self._load_attempt_model(draft_id=intent.context.draft_id)
                if existing is not None:
                    # Outcome owns the global attempt -> context lock order.  Once an
                    # attempt exists, keep this replay path read-only below the
                    # conversation fence so it cannot invert that order.
                    current = await self._load_context_snapshot(
                        command,
                        for_update=False,
                    )
                    self._validate_confirmation(
                        command=command,
                        account_label_value=current.account_label,
                        target_chat_id=current.target_chat_id,
                    )
                    self._validate_attempt_binding(existing, current)
                    replay = self._attempt_result(
                        attempt=_snapshot_attempt(existing),
                        context=current,
                        idempotent_replay=True,
                    )
                else:
                    await self._raise_if_task_send_blocked(
                        conversation_id=intent.context.conversation_id,
                        task_id=intent.context.task_id,
                        draft_id=intent.context.draft_id,
                    )
                    # No active attempt exists in the conversation.  It is now safe
                    # to lock the remaining context rows while retaining the
                    # conversation-first reservation order.
                    current = await self._load_context_snapshot(
                        command,
                        for_update=True,
                    )
                    self._validate_confirmation(
                        command=command,
                        account_label_value=current.account_label,
                        target_chat_id=current.target_chat_id,
                    )
                    if _send_fingerprint(current) != _send_fingerprint(intent.context):
                        raise ReplySendError(
                            code="send_context_changed",
                            message=(
                                "Reply send context changed during validation; "
                                "review and confirm the draft again"
                            ),
                        )
                    reservation = await self._create_reservation(
                        command=command,
                        intent=intent,
                        current=current,
                        probe=probe,
                    )
        except IntegrityError as exc:
            await self._session.rollback()
            return await self._load_concurrent_replay(command, cause=exc)

        if replay is not None:
            return replay
        if reservation is None:
            raise RuntimeError("Reply send reservation did not produce an outcome")
        return reservation

    async def _create_reservation(
        self,
        *,
        command: SendApprovedReplyCommand,
        intent: _SendIntent,
        current: _SendContextSnapshot,
        probe: _ConnectionProbe,
    ) -> _Reservation:
        """Create one durable SENDING attempt inside the active short transaction."""

        self._validate_context_delivery(current)
        origin_id = current.origin_connection_id
        if not origin_id:
            raise ReplySendError(
                code="origin_connection_missing",
                message="The originating Telegram Business connection is missing",
            )
        current_candidates = await self._load_connection_candidates(
            command=command,
            account_id=current.account_id,
            origin_id=origin_id,
            for_update=True,
        )
        if current_candidates != intent.candidates:
            raise ReplySendError(
                code="send_context_changed",
                message=(
                    "Telegram connection state changed during validation; review and confirm again"
                ),
            )
        if (
            probe.connection_id,
            probe.business_connection_id,
        ) not in _candidate_keys(current_candidates):
            raise ReplySendError(
                code="send_context_changed",
                message="Validated Telegram connection is no longer available",
            )

        await self._apply_connection_probe(
            account_id=current.account_id,
            probe=probe,
        )
        covered_task_ids = await self._load_covered_task_ids(
            conversation_id=current.conversation_id,
            account_id=current.account_id,
            project_id=current.project_id,
            for_update=True,
        )
        if covered_task_ids != intent.covered_task_ids:
            raise ReplySendError(
                code="send_context_changed",
                message=(
                    "Unanswered tasks changed during validation; review and confirm the draft again"
                ),
            )
        if current.task_id not in covered_task_ids:
            raise ReplySendError(
                code="task_closed",
                message="Reply task no longer needs a reply",
            )

        now = datetime.now(UTC)
        attempt = SendAttempt(
            id=uuid4(),
            reply_draft_id=current.draft_id,
            reply_task_id=current.task_id,
            conversation_id=current.conversation_id,
            account_id=current.account_id,
            project_id=current.project_id,
            principal_subject=command.principal.subject[:255],
            status=SendAttemptStatus.SENDING.value,
            account_label=current.account_label,
            target_chat_id=current.target_chat_id,
            business_connection_id=probe.business_connection_id,
            reserved_at=now,
            started_at=now,
        )
        self._session.add(attempt)
        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action="reply_send_reserved_from_mcp",
                bot_id=current.bot_id,
                account_id=current.account_id,
                project_id=current.project_id,
                metadata_={
                    "attempt_id": str(attempt.id),
                    "draft_id": str(current.draft_id),
                    "task_id": str(current.task_id),
                    "covered_task_ids": [str(task_id) for task_id in covered_task_ids],
                    "account_label": current.account_label,
                    "target_chat_id": current.target_chat_id,
                    "business_connection_id": probe.business_connection_id,
                },
            )
        )
        return _Reservation(
            context=current,
            attempt=_snapshot_attempt(attempt),
            business_connection_id=probe.business_connection_id,
            covered_task_ids=covered_task_ids,
        )

    async def _load_concurrent_replay(
        self,
        command: SendApprovedReplyCommand,
        *,
        cause: IntegrityError,
    ) -> ReplySendResult:
        async with self._session.begin():
            context = await self._load_context_snapshot(command, for_update=False)
            await self._lock_reservation_conversation(
                command=command,
                conversation_id=context.conversation_id,
            )
            context = await self._load_context_snapshot(command, for_update=False)
            concurrent = await self._load_attempt_model(draft_id=context.draft_id)
            if concurrent is None:
                raise ReplySendError(
                    code="send_reservation_failed",
                    message="Reply send reservation failed safely",
                ) from cause
            self._validate_attempt_binding(concurrent, context)
            return self._attempt_result(
                attempt=_snapshot_attempt(concurrent),
                context=context,
                idempotent_replay=True,
            )

    async def _finalize_failure(
        self,
        *,
        command: SendApprovedReplyCommand,
        reservation: _Reservation,
        status: str,
        error_code: str,
        error_detail: str,
    ) -> ReplySendResult:
        async with self._session.begin():
            attempt = await self._load_attempt_by_id(
                attempt_id=reservation.attempt.id,
                for_update=True,
            )
            if attempt is None:
                raise RuntimeError("Durable reply send reservation disappeared")
            self._validate_attempt_binding(attempt, reservation.context)
            if attempt.status != SendAttemptStatus.SENDING.value:
                return self._attempt_result(
                    attempt=_snapshot_attempt(attempt),
                    context=reservation.context,
                    idempotent_replay=True,
                )

            attempt.status = status
            attempt.error_code = error_code
            attempt.error_detail = error_detail
            attempt.finished_at = datetime.now(UTC)
            self._session.add(
                AuditLog(
                    actor=f"mcp:{command.principal.subject}"[:255],
                    action=f"reply_send_{status}_from_mcp",
                    bot_id=reservation.context.bot_id,
                    account_id=reservation.context.account_id,
                    project_id=reservation.context.project_id,
                    metadata_={
                        "attempt_id": str(attempt.id),
                        "draft_id": str(reservation.context.draft_id),
                        "task_id": str(reservation.context.task_id),
                        "account_label": reservation.context.account_label,
                        "target_chat_id": reservation.context.target_chat_id,
                        "business_connection_id": (reservation.business_connection_id),
                        "error_code": error_code,
                    },
                )
            )
            return self._attempt_result(
                attempt=_snapshot_attempt(attempt),
                context=reservation.context,
            )

    async def _finalize_success(
        self,
        *,
        command: SendApprovedReplyCommand,
        reservation: _Reservation,
        telegram_result: dict[str, Any],
    ) -> ReplySendResult:
        finished_at = datetime.now(UTC)
        async with self._session.begin():
            attempt = await self._load_attempt_by_id(
                attempt_id=reservation.attempt.id,
                for_update=True,
            )
            if attempt is None:
                raise RuntimeError("Durable reply send reservation disappeared")
            self._validate_attempt_binding(attempt, reservation.context)
            if attempt.status != SendAttemptStatus.SENDING.value:
                return self._attempt_result(
                    attempt=_snapshot_attempt(attempt),
                    context=reservation.context,
                    idempotent_replay=True,
                )

            conversation_result = await self._session.execute(
                select(Conversation)
                .where(Conversation.id == reservation.context.conversation_id)
                .with_for_update()
            )
            conversation = conversation_result.scalar_one_or_none()
            if conversation is not None:
                await self._session.execute(
                    select(ReplyTask.id)
                    .where(
                        ReplyTask.id.in_(reservation.covered_task_ids),
                        ReplyTask.conversation_id == reservation.context.conversation_id,
                        ReplyTask.account_id == reservation.context.account_id,
                        ReplyTask.project_id == reservation.context.project_id,
                    )
                    .order_by(ReplyTask.id)
                    .with_for_update()
                )
            draft_result = await self._session.execute(
                select(ReplyDraft)
                .where(ReplyDraft.id == reservation.context.draft_id)
                .with_for_update()
            )
            draft = draft_result.scalar_one_or_none()
            if draft is None or conversation is None:
                raise RuntimeError("Reply send context disappeared after delivery")

            provider_message_id = _provider_message_id(telegram_result)
            attempt.status = SendAttemptStatus.SENT.value
            attempt.provider_message_id = provider_message_id
            attempt.error_code = None
            attempt.error_detail = None
            attempt.finished_at = finished_at

            draft_changed_after_reservation = (
                draft.draft_text != reservation.context.draft_text
                or draft.status != reservation.context.draft_status
            )
            if not draft_changed_after_reservation:
                draft.status = ReplyDraftStatus.APPROVED.value

            await self._session.execute(
                update(ReplyTask)
                .where(
                    ReplyTask.id.in_(reservation.covered_task_ids),
                    ReplyTask.conversation_id == reservation.context.conversation_id,
                    ReplyTask.account_id == reservation.context.account_id,
                    ReplyTask.project_id == reservation.context.project_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                )
                .values(
                    status=ReplyTaskStatus.REPLIED.value,
                    due_at=None,
                    updated_at=finished_at,
                )
            )
            remaining_result = await self._session.execute(
                select(ReplyTask.id)
                .where(
                    ReplyTask.conversation_id == reservation.context.conversation_id,
                    ReplyTask.account_id == reservation.context.account_id,
                    ReplyTask.project_id == reservation.context.project_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                )
                .limit(1)
            )
            has_newer_unanswered = remaining_result.scalar_one_or_none() is not None
            conversation_status_before_outcome = conversation.status
            conversation_requires_reply_before_outcome = conversation.requires_reply
            conversation_terminal_state_preserved = conversation.status in {
                ConversationStatus.CLOSED.value,
                ConversationStatus.MUTED.value,
            }
            if not conversation_terminal_state_preserved:
                conversation.status = (
                    ConversationStatus.OPEN.value
                    if has_newer_unanswered
                    else ConversationStatus.REPLIED.value
                )
                conversation.requires_reply = has_newer_unanswered
            if conversation.last_outbound_at is None or conversation.last_outbound_at < finished_at:
                conversation.last_outbound_at = finished_at
            if conversation.last_message_at is None or conversation.last_message_at < finished_at:
                conversation.last_message_at = finished_at

            self._session.add(
                Message(
                    bot_id=reservation.context.bot_id,
                    gate_event_id=None,
                    account_id=reservation.context.account_id,
                    project_id=reservation.context.project_id,
                    person_id=reservation.context.person_id,
                    direction=MessageDirection.OUTBOUND.value,
                    source="gate-approved-draft",
                    event_type="telegram_business_reply",
                    chat_id=reservation.context.target_chat_id,
                    sender_id=(
                        str(reservation.context.account_telegram_user_id)
                        if reservation.context.account_telegram_user_id is not None
                        else "gate"
                    ),
                    recipient_id=reservation.context.target_chat_id,
                    text=reservation.context.draft_text,
                    business_connection_id=reservation.business_connection_id,
                    payload={
                        **telegram_result,
                        "_gate_counterparty": {
                            "display_name": (reservation.context.counterparty_display_name)
                        },
                    },
                    sent_at=finished_at,
                )
            )
            self._session.add(
                AuditLog(
                    actor=f"mcp:{command.principal.subject}"[:255],
                    action="reply_send_sent_from_mcp",
                    bot_id=reservation.context.bot_id,
                    account_id=reservation.context.account_id,
                    project_id=reservation.context.project_id,
                    metadata_={
                        "attempt_id": str(attempt.id),
                        "draft_id": str(reservation.context.draft_id),
                        "task_id": str(reservation.context.task_id),
                        "covered_task_ids": [
                            str(task_id) for task_id in reservation.covered_task_ids
                        ],
                        "newer_unanswered_preserved": has_newer_unanswered,
                        "conversation_terminal_state_preserved": (
                            conversation_terminal_state_preserved
                        ),
                        "conversation_status_before_outcome": (conversation_status_before_outcome),
                        "conversation_requires_reply_before_outcome": (
                            conversation_requires_reply_before_outcome
                        ),
                        "draft_changed_after_reservation": (draft_changed_after_reservation),
                        "account_label": reservation.context.account_label,
                        "target_chat_id": reservation.context.target_chat_id,
                        "business_connection_id": (reservation.business_connection_id),
                        "provider_message_id": provider_message_id,
                    },
                )
            )
            return self._attempt_result(
                attempt=_snapshot_attempt(attempt),
                context=reservation.context,
            )

    async def _load_context_snapshot(
        self,
        command: SendApprovedReplyCommand,
        *,
        for_update: bool,
    ) -> _SendContextSnapshot:
        draft, task, conversation, trigger, account = await self._load_context_models(
            command,
            for_update=for_update,
        )
        label = account_label(alias=account.alias, country_code=account.country_code)
        payload = trigger.payload if isinstance(trigger.payload, dict) else {}
        raw_snapshot = payload.get("_gate_counterparty")
        counterparty = raw_snapshot if isinstance(raw_snapshot, dict) else {}
        display_name = counterparty.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = conversation.title

        return _SendContextSnapshot(
            draft_id=draft.id,
            draft_text=draft.draft_text,
            draft_status=draft.status,
            draft_updated_at=draft.updated_at,
            task_id=task.id,
            task_status=task.status,
            conversation_id=conversation.id,
            conversation_business_connection_id=(conversation.business_connection_id),
            conversation_last_inbound_at=conversation.last_inbound_at,
            trigger_message_id=trigger.id,
            trigger_business_connection_id=trigger.business_connection_id,
            target_chat_id=trigger.sender_id,
            account_id=draft.account_id,
            project_id=draft.project_id,
            account_alias=account.alias,
            account_label=label,
            account_is_active=account.is_active,
            account_send_enabled=account.send_enabled,
            bot_id=conversation.bot_id,
            person_id=trigger.person_id,
            account_telegram_user_id=account.telegram_user_id,
            counterparty_display_name=display_name,
        )

    async def _load_context_models(
        self,
        command: SendApprovedReplyCommand,
        *,
        for_update: bool,
    ) -> tuple[ReplyDraft, ReplyTask, Conversation, Message, Account]:
        scope = command.principal.access_scope
        statement = (
            select(ReplyDraft, ReplyTask, Conversation, Message, Account)
            .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
            .join(Conversation, ReplyDraft.conversation_id == Conversation.id)
            .join(Message, ReplyDraft.trigger_message_id == Message.id)
            .join(Account, ReplyDraft.account_id == Account.id)
            .options(undefer(ReplyTask.account_id))
            .where(
                ReplyDraft.id == command.draft_id,
                ReplyDraft.account_id.in_(scope.account_ids),
                ReplyDraft.project_id.in_(scope.project_ids),
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Message.account_id.in_(scope.account_ids),
                Message.project_id.in_(scope.project_ids),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=(ReplyDraft, ReplyTask, Message, Account))
        result = await self._session.execute(statement)
        row = result.one_or_none()
        if row is None:
            raise ReplySendError(
                code="draft_not_found",
                message="Reply draft not found in the allowed scope",
            )
        draft, task, conversation, trigger, account = row
        return draft, task, conversation, trigger, account

    async def _lock_reservation_conversation(
        self,
        *,
        command: SendApprovedReplyCommand,
        conversation_id: UUID,
    ) -> None:
        """Serialize reservations before any task or draft row can be locked."""

        scope = command.principal.access_scope
        locked_id = await self._session.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
            )
            .with_for_update()
        )
        if locked_id is None:
            raise ReplySendError(
                code="draft_not_found",
                message="Reply draft not found in the allowed scope",
            )

    async def _load_connection_candidates(
        self,
        *,
        command: SendApprovedReplyCommand,
        account_id: UUID,
        origin_id: str,
        for_update: bool,
    ) -> tuple[_ConnectionCandidate, ...]:
        statement = (
            select(TelegramBusinessConnection)
            .where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.account_id.in_(
                    command.principal.access_scope.account_ids
                ),
            )
            .order_by(
                case(
                    (
                        TelegramBusinessConnection.business_connection_id == origin_id,
                        0,
                    ),
                    else_=1,
                ),
                TelegramBusinessConnection.is_enabled.desc(),
                TelegramBusinessConnection.last_update_at.desc(),
                TelegramBusinessConnection.id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return tuple(
            _ConnectionCandidate(
                id=connection.id,
                account_id=connection.account_id,
                business_connection_id=connection.business_connection_id,
                telegram_user_id=connection.telegram_user_id,
                user_chat_id=connection.user_chat_id,
                rights_fingerprint=_canonical_json_fingerprint(connection.rights),
                is_enabled=connection.is_enabled,
                received_at=connection.received_at,
                last_update_at=connection.last_update_at,
                last_error=connection.last_error,
                created_at=connection.created_at,
                updated_at=connection.updated_at,
            )
            for connection in result.scalars().all()
        )

    async def _load_attempt_model(
        self,
        *,
        draft_id: UUID,
    ) -> SendAttempt | None:
        """Read an attempt without reversing the global outcome lock order.

        Authoritative reservation and concurrent-replay callers already hold the
        draft/task/conversation context lock; preflight uses this only as an advisory
        early read. Outcome processing deliberately locks attempt first and context
        second, so taking an attempt row lock here would create a context -> attempt
        inversion. The conversation lock serializes reservation decisions and the
        unique draft constraint remains the final idempotency guard.
        """

        statement = select(SendAttempt).where(SendAttempt.reply_draft_id == draft_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _load_attempt_by_id(
        self,
        *,
        attempt_id: UUID,
        for_update: bool,
    ) -> SendAttempt | None:
        statement = select(SendAttempt).where(SendAttempt.id == attempt_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _raise_if_task_send_blocked(
        self,
        *,
        conversation_id: UUID,
        task_id: UUID,
        draft_id: UUID,
    ) -> None:
        """Fence overlapping sends without locking attempt rows.

        The authoritative call runs after the joined context query has locked the
        conversation.  Active or ambiguous attempts fence the whole conversation;
        a completed send fences only its original task so a later inbound task in the
        same conversation can still be answered.  This remains a non-locking MVCC read
        to avoid context -> attempt lock inversion with outcome processing.
        """

        statement = (
            select(SendAttempt.id)
            .where(
                SendAttempt.reply_draft_id != draft_id,
                or_(
                    and_(
                        SendAttempt.conversation_id == conversation_id,
                        SendAttempt.status.in_(_ACTIVE_CONVERSATION_ATTEMPT_STATUSES),
                    ),
                    and_(
                        SendAttempt.reply_task_id == task_id,
                        SendAttempt.status == SendAttemptStatus.SENT.value,
                    ),
                ),
            )
            .order_by(SendAttempt.created_at, SendAttempt.id)
            .limit(1)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is not None:
            raise ReplySendError(
                code="task_send_blocked",
                message=(
                    "Another send attempt already blocks this reply task or "
                    "conversation; do not send a second draft"
                ),
            )

    async def _load_covered_task_ids(
        self,
        *,
        conversation_id: UUID,
        account_id: UUID,
        project_id: UUID,
        for_update: bool,
    ) -> tuple[UUID, ...]:
        statement = (
            select(ReplyTask.id)
            .where(
                ReplyTask.conversation_id == conversation_id,
                ReplyTask.account_id == account_id,
                ReplyTask.project_id == project_id,
                ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            )
            .order_by(ReplyTask.created_at, ReplyTask.id)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return tuple(result.scalars().all())

    async def _apply_connection_probe(
        self,
        *,
        account_id: UUID,
        probe: _ConnectionProbe,
    ) -> None:
        now = datetime.now(UTC)
        await self._session.execute(
            update(TelegramBusinessConnection)
            .where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.id != probe.connection_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
            .values(is_enabled=False)
        )
        await self._session.execute(
            update(TelegramBusinessConnection)
            .where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.id == probe.connection_id,
                TelegramBusinessConnection.business_connection_id == probe.business_connection_id,
            )
            .values(
                is_enabled=True,
                rights=probe.rights_dict(),
                last_error=None,
                last_update_at=now,
            )
        )

    def _validate_confirmation(
        self,
        *,
        command: SendApprovedReplyCommand,
        account_label_value: str,
        target_chat_id: str,
    ) -> None:
        if command.expected_account != account_label_value:
            raise ReplySendError(
                code="account_confirmation_mismatch",
                message=(
                    f"Confirmed account {command.expected_account} does not match "
                    f"actual account {account_label_value}"
                ),
            )
        if command.expected_recipient_chat_id != target_chat_id:
            raise ReplySendError(
                code="recipient_confirmation_mismatch",
                message="Confirmed recipient does not match the reply task",
            )

    def _validate_context_delivery(self, context: _SendContextSnapshot) -> None:
        if context.draft_status not in _SENDABLE_DRAFT_STATUSES:
            raise ReplySendError(
                code="draft_closed",
                message="Reply draft is not available for sending",
            )
        if context.task_status != ReplyTaskStatus.NEEDS_REPLY.value:
            raise ReplySendError(
                code="task_closed",
                message="Reply task no longer needs a reply",
            )
        if not context.account_is_active or not context.account_send_enabled:
            raise ReplySendError(
                code="account_send_disabled",
                message=(f"Sending is disabled for account {context.account_label}"),
            )
        if not _runtime_send_enabled(
            settings=self._settings,
            account_label_value=context.account_label,
        ):
            raise ReplySendError(
                code="runtime_send_disabled",
                message=(f"Runtime sending is disabled for account {context.account_label}"),
            )

    def _validate_attempt_binding(
        self,
        attempt: SendAttempt,
        context: _SendContextSnapshot,
    ) -> None:
        if (
            attempt.reply_draft_id != context.draft_id
            or attempt.reply_task_id != context.task_id
            or attempt.conversation_id != context.conversation_id
            or attempt.account_id != context.account_id
            or attempt.project_id != context.project_id
            or attempt.account_label != context.account_label
            or attempt.target_chat_id != context.target_chat_id
        ):
            raise ReplySendError(
                code="send_attempt_binding_mismatch",
                message=("Stored send attempt does not match the confirmed scoped context"),
            )

    def _require_provider_outside_transaction(self) -> None:
        if self._session.in_transaction():
            raise RuntimeError("Telegram provider I/O inside database transaction")

    def _attempt_result(
        self,
        *,
        attempt: _AttemptSnapshot,
        context: _SendContextSnapshot,
        idempotent_replay: bool = False,
    ) -> ReplySendResult:
        return ReplySendResult(
            attempt_id=attempt.id,
            draft_id=attempt.draft_id,
            task_id=attempt.task_id,
            account_id=attempt.account_id,
            project_id=attempt.project_id,
            account_alias=context.account_alias,
            account_label=attempt.account_label,
            recipient_chat_id=attempt.target_chat_id,
            status=attempt.status,
            sent=attempt.status == SendAttemptStatus.SENT.value,
            idempotent_replay=idempotent_replay,
            provider_message_id=attempt.provider_message_id,
            error_code=attempt.error_code,
            error_detail=attempt.error_detail,
        )


def _snapshot_attempt(attempt: SendAttempt) -> _AttemptSnapshot:
    return _AttemptSnapshot(
        id=attempt.id,
        draft_id=attempt.reply_draft_id,
        task_id=attempt.reply_task_id,
        account_id=attempt.account_id,
        project_id=attempt.project_id,
        account_label=attempt.account_label,
        target_chat_id=attempt.target_chat_id,
        status=attempt.status,
        provider_message_id=attempt.provider_message_id,
        error_code=attempt.error_code,
        error_detail=attempt.error_detail,
    )


def _send_fingerprint(context: _SendContextSnapshot) -> tuple[Any, ...]:
    return (
        context.draft_id,
        context.draft_text,
        context.draft_status,
        context.draft_updated_at,
        context.task_id,
        context.task_status,
        context.conversation_id,
        context.conversation_business_connection_id,
        context.conversation_last_inbound_at,
        context.trigger_message_id,
        context.trigger_business_connection_id,
        context.target_chat_id,
        context.account_id,
        context.project_id,
        context.account_alias,
        context.account_label,
        context.account_is_active,
        context.account_send_enabled,
    )


def _candidate_keys(
    candidates: tuple[_ConnectionCandidate, ...],
) -> tuple[tuple[UUID, str], ...]:
    return tuple((candidate.id, candidate.business_connection_id) for candidate in candidates)


def _canonical_json_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _runtime_send_enabled(*, settings: Settings, account_label_value: str) -> bool:
    if not settings.gate_send_enabled:
        return False
    if account_label_value == "RU":
        return settings.gate_account_ru_send_enabled
    if account_label_value == "BY":
        return settings.gate_account_by_send_enabled
    return False


def _delivery_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TelegramApiError):
        server_code = next(
            (
                code
                for code in (exc.http_status, exc.error_code)
                if code is not None and code >= 500
            ),
            None,
        )
        if server_code is not None:
            return SendAttemptStatus.UNKNOWN.value, f"telegram_http_{server_code}"
        if exc.error_code is not None:
            return SendAttemptStatus.FAILED.value, f"telegram_api_{exc.error_code}"
        return SendAttemptStatus.FAILED.value, "telegram_api_error"
    if isinstance(exc, httpx.TimeoutException):
        return SendAttemptStatus.UNKNOWN.value, "telegram_timeout"
    if isinstance(exc, httpx.TransportError):
        return SendAttemptStatus.UNKNOWN.value, "telegram_transport_unknown"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        return SendAttemptStatus.UNKNOWN.value, "telegram_http_5xx"
    return SendAttemptStatus.UNKNOWN.value, "telegram_unconfirmed"


def _safe_error_detail(exc: Exception) -> str:
    if isinstance(exc, TelegramApiError):
        return exc.description[:512]
    if isinstance(exc, httpx.HTTPStatusError):
        return f"telegram_http_{exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "telegram_timeout"
    if isinstance(exc, httpx.TransportError):
        return "telegram_transport_error"
    return exc.__class__.__name__[:128]


def _provider_message_id(result: dict[str, Any]) -> str | None:
    payload = result.get("result")
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("message_id")
    return str(message_id) if message_id is not None else None
