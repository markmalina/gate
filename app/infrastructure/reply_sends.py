"""SQLAlchemy and Telegram adapter for explicit idempotent reply delivery."""

import hashlib
import json
from copy import deepcopy
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
from app.application.telegram_formatting import (
    CAPTION_FORMAT_KEY,
    FORMAT_KEY,
    FormattingError,
    FormattingPreview,
    stored_preview,
)
from app.application.telegram_rich import RichPreview
from app.core.config import Settings
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    PersonalTelegramSendFence,
    ReplyDocument,
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
_SENDABLE_TASK_STATUSES = frozenset(
    {
        ReplyTaskStatus.NEEDS_REPLY.value,
        ReplyTaskStatus.REPLIED.value,
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
    document_filename: str | None = None
    document_media_type: str | None = None
    document_size_bytes: int | None = None
    document_sha256: str | None = None
    document_caption: str | None = None
    document_data: bytes | None = None
    document_expires_at: datetime | None = None
    formatting: FormattingPreview | None = None
    media: dict[str, Any] | None = None

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
        self._rich_receipt_context: dict[UUID, tuple[str, str]] = {}
        self._media_receipts: dict[UUID, dict[str, Any]] = {}

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
        assessment: dict[str, Any] | None = None
        provider_started = False
        try:
            context = reservation.context
            self._validate_reserved_formatting(context)
            if context.media is not None:
                self._validate_context_delivery(context)
                media = context.media
                items = list(media["items"])
                kwargs = {
                    "chat_id": context.target_chat_id,
                    "business_connection_id": reservation.business_connection_id,
                }
                provider_started = True
                if media["mode"] == "photo":
                    item = items[0]
                    telegram_result = await self._telegram.send_business_photo(
                        **kwargs,
                        filename=item["filename"],
                        media_type=item["media_type"],
                        data=item["data"],
                        caption=item["caption"],
                        caption_entities=item["caption_entities"],
                    )
                elif media["mode"] == "album":
                    telegram_result = await self._telegram.send_business_album(
                        **kwargs, items=items
                    )
                else:
                    telegram_result = await self._telegram.send_business_rich_media(
                        **kwargs,
                        blocks=deepcopy(media["snapshot"]["rich"]["blocks"]),
                        items=items,
                    )
                assessment = _assess_media_provider_result(
                    telegram_result, context, reservation.business_connection_id
                )
                if not assessment["complete"]:
                    return await self._finalize_failure(
                        command=command,
                        reservation=reservation,
                        status=SendAttemptStatus.UNKNOWN.value,
                        error_code="media_ack_unconfirmed",
                        error_detail="media_ack_unconfirmed",
                        media_evidence=assessment,
                    )
            elif context.document_sha256 is not None:
                if (
                    context.document_data is None
                    or context.document_filename is None
                    or context.document_media_type is None
                    or context.document_caption is None
                ):
                    raise RuntimeError("Reserved document data disappeared")
                telegram_result = await self._telegram.send_business_document(
                    chat_id=context.target_chat_id,
                    business_connection_id=reservation.business_connection_id,
                    filename=context.document_filename,
                    media_type=context.document_media_type,
                    data=context.document_data,
                    caption=context.document_caption,
                    **(
                        {"caption_entities": list(context.formatting.entities)}
                        if context.formatting is not None
                        else {}
                    ),
                )
                _validate_document_provider_result(
                    telegram_result,
                    context,
                    reservation.business_connection_id,
                )
            elif isinstance(context.formatting, RichPreview):
                if reservation.business_connection_id != context.formatting.scope["connection_id"]:
                    raise ReplySendError(
                        code="rich_connection_changed",
                        message="Rich approval cannot transfer to another Business connection",
                    )
                telegram_result = await self._telegram.send_rich_message(
                    chat_id=context.target_chat_id,
                    business_connection_id=reservation.business_connection_id,
                    blocks=list(context.formatting.blocks),
                )
                _validate_rich_provider_result(
                    telegram_result, context, reservation.business_connection_id
                )
            elif context.formatting is not None and context.formatting.text_format == "markdown":
                telegram_result = await self._telegram.send_formatted_message(
                    chat_id=context.target_chat_id,
                    text=context.formatting.text,
                    entities=list(context.formatting.entities),
                    business_connection_id=reservation.business_connection_id,
                )
            else:
                telegram_result = await self._telegram.send_plain_message(
                    chat_id=context.target_chat_id,
                    text=context.draft_text,
                    business_connection_id=reservation.business_connection_id,
                )
            if telegram_result.get("ok") is not True:
                raise RuntimeError("Telegram API did not confirm delivery")
            if context.media is None and _provider_message_id(telegram_result) is None:
                raise RuntimeError("Telegram API did not return a confirmed message id")
        except Exception as exc:
            status, error_code = _delivery_failure(exc)
            if reservation.context.media is not None and not provider_started:
                status, error_code = SendAttemptStatus.FAILED.value, "media_pre_dispatch_rejected"
            return await self._finalize_failure(
                command=command,
                reservation=reservation,
                status=status,
                error_code=error_code,
                error_detail=error_code
                if isinstance(reservation.context.formatting, RichPreview)
                or reservation.context.media is not None
                else _safe_error_detail(exc),
                media_evidence=assessment if reservation.context.media else None,
            )

        try:
            return await self._finalize_success(
                command=command,
                reservation=reservation,
                telegram_result=telegram_result,
            )
        except Exception:
            if (
                not isinstance(reservation.context.formatting, RichPreview)
                and reservation.context.media is None
            ):
                raise
            # Provider accepted, but receipt transaction failed or its commit
            # outcome is ambiguous. Never label this as a safe failed-to-send.
            await self._session.rollback()
            return await self._finalize_failure(
                command=command,
                reservation=reservation,
                status=SendAttemptStatus.UNKNOWN.value,
                error_code="media_receipt_pending"
                if reservation.context.media
                else "rich_receipt_pending",
                error_detail="media_receipt_pending"
                if reservation.context.media
                else "rich_receipt_pending",
                provider_message_id=_provider_message_id(telegram_result),
                media_evidence=assessment if reservation.context.media else None,
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
                context=context,
            )
            existing = await self._load_attempt_model(draft_id=context.draft_id)
            if existing is not None:
                self._validate_attempt_binding(existing, context)
                await self._validate_saved_payload_binding(existing, context)
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
            if (
                isinstance(intent.context.formatting, RichPreview)
                and candidate.business_connection_id
                != intent.context.formatting.scope["connection_id"]
            ):
                continue
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
                        context=current,
                    )
                    self._validate_attempt_binding(existing, current)
                    await self._validate_saved_payload_binding(existing, current)
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
                        context=current,
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
        if (
            isinstance(current.formatting, RichPreview)
            and probe.business_connection_id != current.formatting.scope["connection_id"]
        ):
            raise ReplySendError(
                code="rich_connection_changed",
                message="Rich approval requires the exact saved Business connection",
            )
        if (
            current.media is not None
            and probe.business_connection_id != current.media["snapshot"]["scope"]["connection_id"]
        ):
            raise ReplySendError(
                code="media_connection_changed",
                message="Media approval requires the saved Business connection",
            )
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
        if (
            current.task_status == ReplyTaskStatus.NEEDS_REPLY.value
            and current.task_id not in covered_task_ids
        ):
            raise ReplySendError(
                code="task_closed",
                message="Reply task no longer needs a reply",
            )
        if current.task_status == ReplyTaskStatus.REPLIED.value and covered_task_ids:
            raise ReplySendError(
                code="task_superseded",
                message=(
                    "A newer inbound reply task exists; review that context and "
                    "confirm a draft for the current task"
                ),
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
        await self._reserve_execution_budget(command, attempt, current)
        if (
            isinstance(current.formatting, RichPreview)
            or current.media is not None
            or command.execution_id
        ):
            self._rich_receipt_context[attempt.id] = (
                self._settings.deployment_sha,
                now.isoformat(),
            )
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
                    "payload_sha256": (
                        current.media["package_sha256"]
                        if current.media
                        else (current.formatting.payload_sha256 if current.formatting else None)
                    ),
                    **(
                        {
                            "delivery_mode": current.media["mode"] if current.media else "rich",
                            "rich_revision": current.formatting.revision
                            if isinstance(current.formatting, RichPreview)
                            else 1,
                            "runtime_revision": self._settings.deployment_sha,
                            "reservation_time": now.isoformat(),
                        }
                        if isinstance(current.formatting, RichPreview)
                        or current.media is not None
                        or command.execution_id
                        else {}
                    ),
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
            self._validate_confirmation(
                command=command,
                account_label_value=context.account_label,
                target_chat_id=context.target_chat_id,
                context=context,
            )
            if concurrent is None:
                raise ReplySendError(
                    code="send_reservation_failed",
                    message="Reply send reservation failed safely",
                ) from cause
            self._validate_attempt_binding(concurrent, context)
            await self._validate_saved_payload_binding(concurrent, context)
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
        provider_message_id: str | None = None,
        media_evidence: dict[str, Any] | None = None,
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
            if provider_message_id is not None:
                attempt.provider_message_id = provider_message_id
            attempt.finished_at = datetime.now(UTC)
            if reservation.context.media is not None:
                await self._persist_media_receipt(reservation, status, media_evidence, error_code)
            if reservation.context.document_sha256 is not None:
                await self._session.execute(
                    update(ReplyDocument)
                    .where(ReplyDocument.reply_draft_id == reservation.context.draft_id)
                    .values(data=None)
                )
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
            if reservation.context.media is not None:
                assessment = _assess_media_provider_result(
                    telegram_result, reservation.context, reservation.business_connection_id
                )
                provider_message_id = str(assessment["messages"][0]["message_id"])
                await self._persist_media_receipt(
                    reservation, SendAttemptStatus.SENT.value, assessment, None
                )
            attempt.status = SendAttemptStatus.SENT.value
            attempt.provider_message_id = provider_message_id
            attempt.error_code = None
            attempt.error_detail = None
            attempt.finished_at = finished_at
            if reservation.context.document_sha256 is not None:
                await self._session.execute(
                    update(ReplyDocument)
                    .where(ReplyDocument.reply_draft_id == reservation.context.draft_id)
                    .values(data=None)
                )

            snapshot_key = (
                CAPTION_FORMAT_KEY
                if reservation.context.document_sha256 is not None
                else FORMAT_KEY
            )
            current_metadata = (
                draft.prompt_context if isinstance(draft.prompt_context, dict) else {}
            )
            reserved_formatting = (
                reservation.context.formatting.to_dict() if reservation.context.formatting else None
            )
            draft_changed_after_reservation = (
                (
                    reservation.context.media is not None
                    and current_metadata.get("_gate_media_package")
                    != reservation.context.media["sentinel"]
                )
                or draft.draft_text != reservation.context.draft_text
                or draft.status != reservation.context.draft_status
                or _canonical_json_fingerprint(current_metadata.get(snapshot_key))
                != _canonical_json_fingerprint(reserved_formatting)
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

            if reservation.context.media is None:
                self._session.add(
                    Message(
                        bot_id=reservation.context.bot_id,
                        gate_event_id=None,
                        account_id=reservation.context.account_id,
                        project_id=reservation.context.project_id,
                        person_id=reservation.context.person_id,
                        direction=MessageDirection.OUTBOUND.value,
                        source=(
                            "gate-approved-document"
                            if reservation.context.document_sha256 is not None
                            else "gate-approved-draft"
                        ),
                        event_type=(
                            "telegram_business_document"
                            if reservation.context.document_sha256 is not None
                            else "telegram_business_reply"
                        ),
                        chat_id=reservation.context.target_chat_id,
                        sender_id=(
                            str(reservation.context.account_telegram_user_id)
                            if reservation.context.account_telegram_user_id is not None
                            else "gate"
                        ),
                        recipient_id=reservation.context.target_chat_id,
                        telegram_message_id=(
                            int(provider_message_id)
                            if provider_message_id is not None and provider_message_id.isdigit()
                            else None
                        ),
                        text=(
                            reservation.context.document_caption
                            if reservation.context.document_sha256 is not None
                            else reservation.context.draft_text
                        ),
                        business_connection_id=reservation.business_connection_id,
                        payload={
                            **telegram_result,
                            "_gate_counterparty": {
                                "display_name": (reservation.context.counterparty_display_name)
                            },
                            "_gate_document_sha256": reservation.context.document_sha256,
                            "_gate_payload_sha256": (
                                reservation.context.formatting.payload_sha256
                                if reservation.context.formatting
                                else None
                            ),
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

    async def _load_media_snapshot(
        self,
        command: SendApprovedReplyCommand,
        draft: ReplyDraft,
        raw_context: dict[str, Any],
        conversation: Conversation,
        trigger: Message,
        *,
        for_update: bool,
    ) -> dict[str, Any] | None:
        from app.application.reply_media import (
            MEDIA_PACKAGE_KEY,
            PreparedMediaItem,
            media_package_digest,
        )
        from app.application.telegram_rich_media import validate_rich_media_attachments
        from app.models.reply_media import ReplyMediaItem, ReplyMediaPackage

        marker = raw_context.get(MEDIA_PACKAGE_KEY)
        is_media = draft.source == "chatgpt-mcp-media" or marker is not None
        if not is_media:
            if command.expected_media_package_sha256 is not None:
                raise ReplySendError(
                    code="media_binding_mismatch", message="Draft is not a prepared media package"
                )
            return None
        statement = select(ReplyMediaPackage).where(ReplyMediaPackage.reply_draft_id == draft.id)
        if for_update:
            statement = statement.with_for_update()
        package = await self._session.scalar(statement)
        if package is None:
            raise ReplySendError(
                code="media_binding_mismatch", message="Prepared media package is missing"
            )
        item_statement = (
            select(ReplyMediaItem)
            .where(ReplyMediaItem.package_id == package.id)
            .order_by(ReplyMediaItem.ordinal)
        )
        if for_update:
            item_statement = item_statement.with_for_update()
        rows = list((await self._session.scalars(item_statement)).all())
        snapshot = deepcopy(package.snapshot_json)
        expected_scope = {
            "workspace_id": command.principal.access_scope.workspace_id,
            "account_id": str(draft.account_id),
            "project_id": str(draft.project_id),
            "principal_subject": command.principal.subject,
            "recipient_chat_id": trigger.sender_id,
            "connection_id": conversation.business_connection_id or trigger.business_connection_id,
            "draft_id": str(draft.id),
            "draft_revision": 1,
            "execution_id": package.execution_id,
        }
        sentinel = {
            "package_id": str(package.id),
            "package_sha256": package.package_sha256,
            "mode": package.mode,
        }
        if (
            package.workspace_id != command.principal.access_scope.workspace_id
            or package.principal_subject != command.principal.subject
            or snapshot.get("scope") != expected_scope
            or marker != sentinel
            or snapshot.get("package_id") != str(package.id)
            or snapshot.get("mode") != package.mode
            or snapshot.get("package_sha256") != package.package_sha256
            or media_package_digest(snapshot) != package.package_sha256
            or snapshot.get("draft_text") != draft.draft_text
            or snapshot.get("schema_version") != "gate.media.v1"
            or snapshot.get("converter_version") != "gate.media-package.v1"
        ):
            raise ReplySendError(
                code="media_scope_changed",
                message="Saved media identity, approval or scope changed",
            )
        metadata = []
        items = []
        for ordinal, item in enumerate(rows, 1):
            if item.ordinal != ordinal:
                raise ReplySendError(
                    code="media_binding_mismatch", message="Prepared media order changed"
                )
            meta = PreparedMediaItem(
                media_ref=str(item.id),
                ordinal=item.ordinal,
                filename=item.filename,
                media_type=item.media_type,
                source_size_bytes=item.source_size_bytes,
                size_bytes=item.size_bytes,
                source_sha256=item.source_sha256,
                output_sha256=item.output_sha256,
                width=item.width,
                height=item.height,
                transform_version=item.transform_version,
                transform=item.transform,
                caption=item.caption,
                caption_format=item.caption_format,
                caption_formatting_json=deepcopy(item.caption_formatting_json),
            ).to_dict()
            try:
                formatting = stored_preview(
                    item.caption,
                    {CAPTION_FORMAT_KEY: item.caption_formatting_json},
                    key=CAPTION_FORMAT_KEY,
                    max_length=1024,
                    allow_empty=True,
                )
            except FormattingError as exc:
                raise ReplySendError(code=exc.code, message=exc.message) from exc
            if formatting is None or formatting.text_format != item.caption_format:
                raise ReplySendError(
                    code="media_changed", message="Prepared caption formatting changed"
                )
            metadata.append(meta)
            items.append(
                {
                    **meta,
                    "id": item.id,
                    "data": item.data,
                    "caption": formatting.text,
                    "caption_entities": list(formatting.entities),
                }
            )
        expected_count = len(items) if package.mode == "album" else 1
        if (
            not 1 <= len(items) <= 10
            or snapshot.get("items") != metadata
            or package.expected_message_count != expected_count
            or snapshot.get("expected_message_count") != expected_count
            or (package.mode in {"photo", "rich_photo"} and len(items) != 1)
            or (package.mode in {"album", "rich_slideshow", "rich_collage"} and len(items) < 2)
        ):
            raise ReplySendError(
                code="media_changed", message="Saved media metadata or cardinality changed"
            )
        if package.mode.startswith("rich_"):
            rich = snapshot.get("rich")
            if (
                not isinstance(rich, dict)
                or rich.get("schema_version") != "gate.rich.v2"
                or rich.get("converter_version") != "gate.telegram-rich-media.v1"
            ):
                raise ReplySendError(
                    code="media_changed", message="Saved Rich media version changed"
                )
            mode_block = {
                "rich_photo": "photo",
                "rich_slideshow": "slideshow",
                "rich_collage": "collage",
            }[package.mode]
            media_blocks = [
                b
                for b in rich["blocks"]
                if isinstance(b, dict) and b.get("type") in {"photo", "slideshow", "collage"}
            ]
            if len(media_blocks) != 1 or media_blocks[0].get("type") != mode_block:
                raise ReplySendError(
                    code="media_changed", message="Media mode and native blocks disagree"
                )
            try:
                validate_rich_media_attachments(
                    rich["blocks"], {f"photo_{n}" for n in range(1, len(items) + 1)}
                )
            except (FormattingError, KeyError) as exc:
                raise ReplySendError(
                    code="media_changed", message="Saved media attachment mapping changed"
                ) from exc
        elif snapshot.get("rich") is not None:
            raise ReplySendError(
                code="media_changed", message="Ordinary media cannot contain Rich blocks"
            )
        if package.receipt_json:
            self._media_receipts[package.id] = deepcopy(package.receipt_json)
        return {
            "package_id": package.id,
            "package_sha256": package.package_sha256,
            "mode": package.mode,
            "snapshot": snapshot,
            "items": tuple(items),
            "expires_at": package.expires_at,
            "sentinel": sentinel,
            "expected_message_count": expected_count,
        }

    async def _reserve_execution_budget(
        self,
        command: SendApprovedReplyCommand,
        attempt: SendAttempt,
        context: _SendContextSnapshot,
    ) -> None:
        if command.execution_id is None and context.media is None:
            return
        from app.models.reply_media import ReplyMediaBudgetReservation, ReplyMediaExecutionBudget

        budget = await self._session.scalar(
            select(ReplyMediaExecutionBudget)
            .where(
                ReplyMediaExecutionBudget.workspace_id
                == command.principal.access_scope.workspace_id,
                ReplyMediaExecutionBudget.principal_subject == command.principal.subject,
                ReplyMediaExecutionBudget.execution_id == command.execution_id,
            )
            .with_for_update()
        )
        messages = context.media["expected_message_count"] if context.media else 1
        if (
            budget is None
            or budget.expires_at <= datetime.now(UTC)
            or budget.write_cap != 12
            or budget.message_cap != 20
        ):
            raise ReplySendError(
                code="media_execution_expired",
                message="Current bounded execution budget is required",
            )
        if context.media is not None and (
            context.media["snapshot"].get("execution_expires_at") != budget.expires_at.isoformat()
            or context.media["expires_at"] > budget.expires_at
        ):
            raise ReplySendError(
                code="media_execution_changed",
                message="Saved execution deadline or asset expiry differs from its budget",
            )
        if (
            budget.writes_reserved + 1 > budget.write_cap
            or budget.messages_reserved + messages > budget.message_cap
        ):
            raise ReplySendError(
                code="media_execution_budget_exhausted",
                message="Provider operation or visible message budget is exhausted",
            )
        budget.writes_reserved += 1
        budget.messages_reserved += messages
        await self._session.flush()
        self._session.add(
            ReplyMediaBudgetReservation(
                send_attempt_id=attempt.id, budget_id=budget.id, expected_messages=messages
            )
        )
        await self._session.flush()

    async def _persist_media_receipt(
        self,
        reservation: _Reservation,
        status: str,
        evidence: dict[str, Any] | None,
        error_code: str | None,
    ) -> None:
        from app.models.reply_media import ReplyMediaItem, ReplyMediaPackage

        media = reservation.context.media
        assert media is not None
        package = await self._session.scalar(
            select(ReplyMediaPackage)
            .where(ReplyMediaPackage.id == media["package_id"])
            .with_for_update()
        )
        if package is None:
            raise RuntimeError("Durable media package disappeared")
        receipt = _empty_media_receipt(media, status)
        runtime, reserved_at = self._rich_receipt_context.get(reservation.attempt.id, (None, None))
        receipt.update(
            {
                "attempt_id": str(reservation.attempt.id),
                "runtime_revision": runtime,
                "reservation_time": reserved_at,
                "error_code": error_code,
            }
        )
        if evidence is not None:
            receipt.update(
                {
                    "confirmed_message_count": len(evidence["messages"]),
                    "unknown_message_count": max(
                        0, receipt["expected_message_count"] - len(evidence["messages"])
                    ),
                    "provider_message_ids": [str(m["message_id"]) for m in evidence["messages"]],
                    "items": evidence["items"],
                }
            )
        if status == "failed":
            receipt["unknown_message_count"] = 0
            for item in receipt["items"]:
                item["outcome"] = "failed"
        receipt["confirmed_item_count"] = sum(
            item["outcome"] == "sent" for item in receipt["items"]
        )
        receipt["unknown_item_count"] = sum(
            item["outcome"] == "unknown" for item in receipt["items"]
        )
        package.receipt_json = deepcopy(receipt)
        self._media_receipts[package.id] = deepcopy(receipt)
        rows = list(
            (
                await self._session.scalars(
                    select(ReplyMediaItem)
                    .where(ReplyMediaItem.package_id == package.id)
                    .order_by(ReplyMediaItem.ordinal)
                    .with_for_update()
                )
            ).all()
        )
        for row, item_receipt in zip(rows, receipt["items"], strict=True):
            row.receipt_json = deepcopy(item_receipt)
            row.provider_message_id = item_receipt.get("provider_message_id")
        # Retain only route-validated provider messages; one outward row per
        # album message, never a synthetic inbound task or a phantom success.
        for message in evidence["messages"] if evidence else []:
            assert evidence is not None
            position = evidence["message_positions"].get(message["message_id"])
            self._session.add(
                Message(
                    bot_id=reservation.context.bot_id,
                    gate_event_id=None,
                    account_id=reservation.context.account_id,
                    project_id=reservation.context.project_id,
                    person_id=reservation.context.person_id,
                    direction=MessageDirection.OUTBOUND.value,
                    source="gate-approved-media",
                    event_type="telegram_business_media",
                    chat_id=reservation.context.target_chat_id,
                    sender_id=str(reservation.context.account_telegram_user_id)
                    if reservation.context.account_telegram_user_id is not None
                    else "gate",
                    recipient_id=reservation.context.target_chat_id,
                    telegram_message_id=message["message_id"],
                    text=message.get(
                        "caption",
                        media["snapshot"].get("rich", {}).get("semantic_preview", "")
                        if media["snapshot"].get("rich")
                        else "",
                    ),
                    business_connection_id=reservation.business_connection_id,
                    payload={
                        "ok": True,
                        "result": deepcopy(message),
                        "_gate_media_package_id": str(package.id),
                        "_gate_media_package_sha256": media["package_sha256"],
                        "_gate_media_position": position,
                        "_gate_outcome": status,
                        "_gate_counterparty": {
                            "display_name": reservation.context.counterparty_display_name
                        },
                    },
                    sent_at=datetime.now(UTC),
                )
            )
        await self._session.flush()

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

        raw_context = draft.prompt_context if isinstance(draft.prompt_context, dict) else {}
        is_document = (
            raw_context.get("kind") == "document" or draft.source == "chatgpt-mcp-document"
        )
        document = None
        if is_document or command.expected_document_sha256 is not None:
            document_statement = select(ReplyDocument).where(
                ReplyDocument.reply_draft_id == draft.id
            )
            if for_update:
                document_statement = document_statement.with_for_update()
            document = await self._session.scalar(document_statement)
        if is_document != (document is not None):
            raise ReplySendError(
                code="document_binding_mismatch",
                message="Document preparation is incomplete",
            )
        if is_document != (command.expected_document_sha256 is not None):
            raise ReplySendError(
                code="document_confirmation_required",
                message="Use the matching exact document confirmation",
            )
        if document is not None and document.principal_subject != command.principal.subject[:255]:
            raise ReplySendError(
                code="document_scope_mismatch",
                message="Document belongs to another authenticated principal",
            )

        media = await self._load_media_snapshot(
            command, draft, raw_context, conversation, trigger, for_update=for_update
        )
        try:
            formatting = (
                None
                if media is not None
                else stored_preview(
                    document.caption if document else draft.draft_text,
                    raw_context,
                    key=CAPTION_FORMAT_KEY if document else FORMAT_KEY,
                    max_length=1024 if document else 4096,
                    allow_empty=document is not None,
                )
            )
        except FormattingError as exc:
            raise ReplySendError(code=exc.code, message=exc.message) from exc

        if isinstance(formatting, RichPreview):
            expected_scope = {
                "workspace_id": command.principal.access_scope.workspace_id,
                "account_id": str(draft.account_id),
                "project_id": str(draft.project_id),
                "recipient_chat_id": trigger.sender_id,
                "connection_id": conversation.business_connection_id
                or trigger.business_connection_id,
                "draft_id": str(draft.id),
            }
            if formatting.scope != expected_scope:
                raise ReplySendError(
                    code="rich_scope_changed", message="Saved rich approval scope changed"
                )
        return _SendContextSnapshot(
            formatting=formatting,
            media=media,
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
            document_filename=document.filename if document else None,
            document_media_type=document.media_type if document else None,
            document_size_bytes=document.size_bytes if document else None,
            document_sha256=document.sha256 if document else None,
            document_caption=document.caption if document else None,
            document_data=document.data if document else None,
            document_expires_at=document.expires_at if document else None,
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
        conversation fence; preflight uses this only as an advisory early read. A new
        reservation takes child context locks only after it proves no active attempt
        exists, while replay deliberately keeps those child reads unlocked. Outcome
        processing locks attempt first and conversation second, so taking an attempt
        row lock here would create the inverse order. The unique draft constraint
        remains the final idempotency guard.
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
        """Fence only overlapping or ambiguous sends, not completed follow-ups.

        The authoritative call runs after the joined context query has locked the
        conversation. Active or ambiguous attempts fence the whole conversation.
        A completed send does not fence another separately persisted and explicitly
        confirmed draft for the same task. This remains a non-locking MVCC read to
        avoid context -> attempt lock inversion with outcome processing.
        """

        del task_id  # The task no longer imposes a one-successful-send limit.
        statement = (
            select(SendAttempt.id)
            .where(
                SendAttempt.reply_draft_id != draft_id,
                SendAttempt.conversation_id == conversation_id,
                SendAttempt.status.in_(_ACTIVE_CONVERSATION_ATTEMPT_STATUSES),
            )
            .order_by(SendAttempt.created_at, SendAttempt.id)
            .limit(1)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is not None:
            raise ReplySendError(
                code="task_send_blocked",
                message=(
                    "Another active or ambiguous send attempt blocks this "
                    "conversation until its delivery outcome is known"
                ),
            )
        # Both channels reserve under the same conversation row lock. A
        # personal attempt permanently owns its exact draft; an unresolved
        # personal attempt also fences newer drafts in that conversation.
        personal = await self._session.scalar(
            select(PersonalTelegramSendFence.id)
            .where(
                or_(
                    PersonalTelegramSendFence.reply_draft_id == draft_id,
                    and_(
                        PersonalTelegramSendFence.conversation_id == conversation_id,
                        PersonalTelegramSendFence.status == "unknown",
                    ),
                )
            )
            .limit(1)
        )
        if personal is not None:
            raise ReplySendError(
                code="task_send_blocked",
                message="A personal Telegram reservation blocks this Business draft",
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
        context: _SendContextSnapshot,
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
        if command.expected_media_package_sha256 != (
            context.media["package_sha256"] if context.media else None
        ) or (
            context.media is not None
            and command.execution_id != context.media["snapshot"]["scope"]["execution_id"]
        ):
            raise ReplySendError(
                code="media_confirmation_mismatch",
                message="Exact approved media digest and execution are required",
            )
        if context.formatting is not None:
            if command.expected_payload_sha256 is None:
                raise ReplySendError(
                    code="payload_confirmation_required",
                    message="Confirm the exact saved formatting payload SHA-256 before sending",
                )
            if command.expected_payload_sha256 != context.formatting.payload_sha256:
                raise ReplySendError(
                    code="payload_confirmation_mismatch",
                    message="Confirmed formatting payload differs from the saved preview",
                )
        elif command.expected_payload_sha256 is not None:
            raise ReplySendError(
                code="payload_confirmation_mismatch",
                message="Legacy draft has no saved formatting preview to confirm",
            )
        if (
            command.expected_document_sha256 != context.document_sha256
            or command.expected_document_filename != context.document_filename
            or command.expected_document_size_bytes != context.document_size_bytes
            or command.expected_document_caption != context.document_caption
        ):
            raise ReplySendError(
                code="document_confirmation_mismatch",
                message="Confirmed file or caption differs from prepared document",
            )

    def _validate_context_delivery(self, context: _SendContextSnapshot) -> None:
        if context.media is not None:
            media = context.media
            if media["mode"].startswith("rich_") and not self._settings.gate_rich_send_enabled:
                raise ReplySendError(
                    code="rich_send_disabled", message="Rich Business sending is disabled"
                )
            if media["expires_at"] <= datetime.now(UTC):
                raise ReplySendError(
                    code="media_expired", message="Prepared media expired; prepare again"
                )
            for item in media["items"]:
                if (
                    not isinstance(item["data"], bytes)
                    or len(item["data"]) != item["size_bytes"]
                    or hashlib.sha256(item["data"]).hexdigest() != item["output_sha256"]
                ):
                    raise ReplySendError(
                        code="media_unavailable",
                        message="Prepared media bytes changed or are unavailable",
                    )
        if (
            isinstance(context.formatting, RichPreview)
            and not self._settings.gate_rich_send_enabled
        ):
            raise ReplySendError(
                code="rich_send_disabled", message="Rich Business sending is disabled"
            )
        if context.document_sha256 is not None:
            if not self._settings.gate_document_send_enabled:
                raise ReplySendError(
                    code="document_send_disabled",
                    message="Business document sending is disabled",
                )
            if (
                context.document_data is None
                or context.document_expires_at is None
                or context.document_expires_at <= datetime.now(UTC)
                or context.document_size_bytes != len(context.document_data)
                or hashlib.sha256(context.document_data).hexdigest() != context.document_sha256
                or context.draft_text != context.document_filename
            ):
                raise ReplySendError(
                    code="document_unavailable",
                    message="Document expired or changed; prepare it again",
                )
        if context.draft_status not in _SENDABLE_DRAFT_STATUSES:
            raise ReplySendError(
                code="draft_closed",
                message="Reply draft is not available for sending",
            )
        if context.task_status not in _SENDABLE_TASK_STATUSES:
            raise ReplySendError(
                code="task_closed",
                message="Reply task is not available for an explicit send",
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

    async def _validate_saved_payload_binding(
        self,
        attempt: SendAttempt,
        context: _SendContextSnapshot,
    ) -> None:
        """A replay is bound to the payload recorded with its durable reservation."""
        reservation_metadata = await self._session.scalar(
            select(AuditLog.metadata_)
            .where(
                AuditLog.action == "reply_send_reserved_from_mcp",
                AuditLog.account_id == context.account_id,
                AuditLog.project_id == context.project_id,
                AuditLog.metadata_["attempt_id"].astext == str(attempt.id),
            )
            .limit(1)
        )
        saved_digest = (
            reservation_metadata.get("payload_sha256")
            if isinstance(reservation_metadata, dict)
            else None
        )
        if isinstance(reservation_metadata, dict):
            runtime_revision = reservation_metadata.get("runtime_revision")
            reservation_time = reservation_metadata.get("reservation_time")
            if isinstance(runtime_revision, str) and isinstance(reservation_time, str):
                self._rich_receipt_context[attempt.id] = (runtime_revision, reservation_time)
        current_digest = (
            context.media["package_sha256"]
            if context.media
            else (context.formatting.payload_sha256 if context.formatting else None)
        )
        if saved_digest != current_digest:
            raise ReplySendError(
                code="send_attempt_payload_mismatch",
                message="Stored send attempt belongs to another saved formatting payload",
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

    @staticmethod
    def _validate_reserved_formatting(context: _SendContextSnapshot) -> None:
        """Validate the immutable reservation payload without re-rendering Markdown."""
        if context.formatting is None:
            return
        key = CAPTION_FORMAT_KEY if context.document_sha256 is not None else FORMAT_KEY
        text = context.draft_text
        if context.document_sha256 is not None:
            if context.document_caption is None:
                raise ReplySendError(
                    code="document_binding_mismatch",
                    message="Reserved caption is missing",
                )
            text = context.document_caption
        try:
            stored_preview(
                text,
                {key: context.formatting.to_dict()},
                key=key,
                max_length=1024 if context.document_sha256 is not None else 4096,
                allow_empty=context.document_sha256 is not None,
            )
        except FormattingError as exc:
            raise ReplySendError(code=exc.code, message=exc.message) from exc

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
        rich_ambiguous = (
            isinstance(context.formatting, RichPreview) or context.media is not None
        ) and attempt.status in {
            SendAttemptStatus.SENDING.value,
            SendAttemptStatus.RESERVED.value,
        }
        return ReplySendResult(
            attempt_id=attempt.id,
            draft_id=attempt.draft_id,
            task_id=attempt.task_id,
            account_id=attempt.account_id,
            project_id=attempt.project_id,
            account_alias=context.account_alias,
            account_label=attempt.account_label,
            recipient_chat_id=attempt.target_chat_id,
            status=SendAttemptStatus.UNKNOWN.value if rich_ambiguous else attempt.status,
            sent=attempt.status == SendAttemptStatus.SENT.value,
            idempotent_replay=idempotent_replay,
            provider_message_id=attempt.provider_message_id,
            error_code=("media_receipt_pending" if context.media else "rich_receipt_pending")
            if rich_ambiguous
            else attempt.error_code,
            error_detail=("media_receipt_pending" if context.media else "rich_receipt_pending")
            if rich_ambiguous
            else attempt.error_detail,
            document_filename=context.document_filename,
            document_media_type=context.document_media_type,
            document_size_bytes=context.document_size_bytes,
            document_sha256=context.document_sha256,
            payload_sha256=context.media["package_sha256"]
            if context.media
            else (context.formatting.payload_sha256 if context.formatting else None),
            delivery_mode=context.media["mode"]
            if context.media
            else ("rich" if isinstance(context.formatting, RichPreview) else None),
            draft_revision=context.media["snapshot"]["scope"]["draft_revision"]
            if context.media is not None
            else (
                context.formatting.revision if isinstance(context.formatting, RichPreview) else None
            ),
            runtime_revision=self._rich_receipt_context.get(attempt.id, (None, None))[0],
            reservation_time=self._rich_receipt_context.get(attempt.id, (None, None))[1],
            media=(
                self._media_receipts.get(context.media["package_id"])
                or _empty_media_receipt(
                    context.media, "unknown" if rich_ambiguous else attempt.status
                )
            )
            if context.media
            else None,
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
        context.document_filename,
        context.document_media_type,
        context.document_size_bytes,
        context.document_sha256,
        context.document_caption,
        context.document_expires_at,
        _canonical_json_fingerprint(context.media["snapshot"]) if context.media else None,
        _canonical_json_fingerprint(context.formatting.to_dict())
        if context.formatting is not None
        else None,
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


def _validate_rich_provider_result(
    result: dict[str, Any],
    context: _SendContextSnapshot,
    business_connection_id: str,
) -> None:
    message = result.get("result")
    if not isinstance(message, dict):
        raise RuntimeError("telegram_rich_ack_invalid")
    chat, rich = message.get("chat"), message.get("rich_message")
    if (
        type(message.get("message_id")) is not int
        or message["message_id"] < 1
        or not isinstance(chat, dict)
        or str(chat.get("id")) != context.target_chat_id
        or not isinstance(rich, dict)
        or not isinstance(rich.get("blocks"), list)
        or not rich["blocks"]
        or message.get("business_connection_id") != business_connection_id
    ):
        raise RuntimeError("telegram_rich_ack_mismatch")


def _validate_document_provider_result(
    result: dict[str, Any],
    context: _SendContextSnapshot,
    business_connection_id: str,
) -> None:
    """Reject an inconsistent acknowledgement as an ambiguous outcome."""

    message = result.get("result")
    if not isinstance(message, dict):
        raise RuntimeError("Telegram did not return a document message")
    chat = message.get("chat")
    document = message.get("document")
    if (
        not isinstance(chat, dict)
        or str(chat.get("id")) != context.target_chat_id
        or not isinstance(document, dict)
        or not isinstance(document.get("file_id"), str)
        or not document.get("file_id")
        or not isinstance(document.get("file_unique_id"), str)
        or not document.get("file_unique_id")
        or document.get("file_name") != context.document_filename
        or document.get("mime_type") != context.document_media_type
        or document.get("file_size") != context.document_size_bytes
        or (
            message.get("business_connection_id") is not None
            and message.get("business_connection_id") != business_connection_id
        )
        or (
            message.get("caption") is not None
            and message.get("caption") != context.document_caption
        )
    ):
        raise RuntimeError("Telegram document acknowledgement mismatched preparation")


def _empty_media_receipt(media: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "package_id": str(media["package_id"]),
        "mode": media["mode"],
        "schema_version": "gate.media.v1",
        "package_sha256": media["package_sha256"],
        "expected_message_count": media["expected_message_count"],
        "confirmed_message_count": 0,
        "unknown_message_count": media["expected_message_count"],
        "expected_item_count": len(media["items"]),
        "confirmed_item_count": 0,
        "unknown_item_count": len(media["items"]),
        "status": status,
        "provider_message_ids": [],
        "items": [
            {
                "position": item["ordinal"],
                "media_ref": item["media_ref"],
                "sha256": item["output_sha256"],
                "size_bytes": item["size_bytes"],
                "caption_sha256": _media_item_caption_digest(media, item),
                "outcome": "unknown",
                "provider_message_id": None,
                "provider_media_group_id": None,
            }
            for item in media["items"]
        ],
    }


def _assess_media_provider_result(
    result: dict[str, Any],
    context: _SendContextSnapshot,
    business_connection_id: str,
) -> dict[str, Any]:
    """Assess without throwing away known delivery evidence or retrying.

    Cardinality, unique/increasing IDs, recipient, Business connection, media
    groups and per-position captions must all match before overall SENT.
    Route-valid messages with insufficient item linkage remain explicitly known
    messages with unknown item outcomes.
    """
    media = context.media
    assert media is not None
    receipt_items = _empty_media_receipt(media, "unknown")["items"]
    payload = result.get("result")
    raw_messages = payload if media["mode"] == "album" and isinstance(payload, list) else [payload]
    messages: list[dict[str, Any]] = []
    positions: dict[int, int | None] = {}
    seen: set[int] = set()
    complete = result.get("ok") is True and len(raw_messages) == media["expected_message_count"]
    group_ids: set[str] = set()
    ordered_ids: list[int] = []
    for index, message in enumerate(raw_messages):
        if not isinstance(message, dict):
            complete = False
            continue
        message_id = message.get("message_id")
        chat = message.get("chat")
        valid_route = (
            type(message_id) is int
            and message_id > 0
            and isinstance(chat, dict)
            and str(chat.get("id")) == context.target_chat_id
            and message.get("business_connection_id") == business_connection_id
        )
        if not valid_route or message_id in seen:
            complete = False
            continue
        assert isinstance(message_id, int)
        seen.add(message_id)
        ordered_ids.append(message_id)
        messages.append(deepcopy(message))
        positions[message_id] = None
        if media["mode"] in {"photo", "album"}:
            photos = message.get("photo")
            group_id = message.get("media_group_id")
            if media["mode"] == "album":
                if not isinstance(group_id, str) or not group_id:
                    complete = False
                    continue
                group_ids.add(group_id)
            if (
                index >= len(media["items"])
                or not _valid_provider_photo_sizes(photos)
                or message.get("caption", "") != media["items"][index]["caption"]
                or message.get("caption_entities", []) != media["items"][index]["caption_entities"]
            ):
                complete = False
                continue
            receipt_items[index].update(
                {
                    "outcome": "sent",
                    "provider_message_id": str(message_id),
                    "provider_media_group_id": group_id,
                }
            )
            positions[message_id] = index + 1
        else:
            rich = message.get("rich_message")
            actual_blocks = rich.get("blocks") if isinstance(rich, dict) else None
            expected_blocks = media["snapshot"]["rich"]["blocks"]
            if not _rich_media_ack_matches(actual_blocks, expected_blocks):
                complete = False
                continue
            for item in receipt_items:
                item.update({"outcome": "sent", "provider_message_id": str(message_id)})
    if media["mode"] == "album" and (len(group_ids) != 1 or ordered_ids != sorted(ordered_ids)):
        complete = False
        # A wrong group/order cannot prove the requested album item linkage.
        for item in receipt_items:
            item.update(
                {"outcome": "unknown", "provider_message_id": None, "provider_media_group_id": None}
            )
        positions = dict.fromkeys(positions)
    complete = (
        complete
        and len(messages) == media["expected_message_count"]
        and all(item["outcome"] == "sent" for item in receipt_items)
    )
    return {
        "complete": complete,
        "messages": messages,
        "items": receipt_items,
        "message_positions": positions,
    }


def _valid_provider_photo_sizes(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(photo, dict)
            and isinstance(photo.get("file_id"), str)
            and bool(photo["file_id"])
            and isinstance(photo.get("file_unique_id"), str)
            and bool(photo["file_unique_id"])
            and type(photo.get("width")) is int
            and photo["width"] > 0
            and type(photo.get("height")) is int
            and photo["height"] > 0
            for photo in value
        )
    )


def _rich_media_ack_matches(actual: Any, expected: list[dict[str, Any]]) -> bool:
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    for got, wanted in zip(actual, expected, strict=True):
        if not isinstance(got, dict) or got.get("type") != wanted["type"]:
            return False
        if wanted["type"] == "photo":
            if not _valid_provider_photo_sizes(got.get("photo")) or got.get(
                "caption"
            ) != wanted.get("caption"):
                return False
        elif wanted["type"] in {"slideshow", "collage"}:
            if got.get("caption") != wanted.get("caption") or not _rich_media_ack_matches(
                got.get("blocks"), wanted["blocks"]
            ):
                return False
        elif got != wanted:
            return False
    return True


def _media_item_caption_digest(media: dict[str, Any], item: dict[str, Any]) -> str:
    rich = media["snapshot"].get("rich")
    if not rich:
        return str(item["caption_formatting_json"]["payload_sha256"])
    target = f"attach://photo_{item['ordinal']}"
    for block in rich["blocks"]:
        children = (
            block.get("blocks", []) if block.get("type") in {"slideshow", "collage"} else [block]
        )
        for photo in children:
            if photo.get("type") == "photo" and photo["photo"]["media"] == target:
                return _canonical_json_fingerprint(photo.get("caption"))
    raise RuntimeError("Saved photo caption identity disappeared")
