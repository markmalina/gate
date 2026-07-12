"""SQLAlchemy and Telegram adapter for explicit idempotent reply delivery."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select, update
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
from app.services.telegram import TelegramClient


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
        draft, task, conversation, trigger, account = await self._load_context(command)
        label = account_label(alias=account.alias, country_code=account.country_code)
        target_chat_id = trigger.sender_id
        self._validate_confirmation(
            command=command,
            account_label_value=label,
            target_chat_id=target_chat_id,
        )
        existing = await self._load_attempt(draft.id)
        if existing is not None:
            return self._attempt_result(
                attempt=existing,
                draft=draft,
                task=task,
                account=account,
                idempotent_replay=True,
            )

        connection = await self._load_origin_connection(
            command=command,
            draft=draft,
            conversation=conversation,
            trigger=trigger,
        )
        self._validate_delivery(
            draft=draft,
            task=task,
            account=account,
            account_label_value=label,
            connection=connection,
        )

        now = datetime.now(UTC)
        attempt = SendAttempt(
            id=uuid4(),
            reply_draft_id=draft.id,
            reply_task_id=task.id,
            conversation_id=conversation.id,
            account_id=draft.account_id,
            project_id=draft.project_id,
            principal_subject=command.principal.subject[:255],
            status=SendAttemptStatus.SENDING.value,
            account_label=label,
            target_chat_id=target_chat_id,
            business_connection_id=connection.business_connection_id,
            reserved_at=now,
            started_at=now,
        )
        self._session.add(attempt)
        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action="reply_send_reserved_from_mcp",
                bot_id=conversation.bot_id,
                account_id=draft.account_id,
                project_id=draft.project_id,
                metadata_={
                    "attempt_id": str(attempt.id),
                    "draft_id": str(draft.id),
                    "task_id": str(task.id),
                    "account_label": label,
                    "target_chat_id": target_chat_id,
                    "business_connection_id": connection.business_connection_id,
                },
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            concurrent = await self._load_attempt(draft.id)
            if concurrent is None:
                raise ReplySendError(
                    code="send_reservation_failed",
                    message="Reply send reservation failed safely",
                ) from exc
            return self._attempt_result(
                attempt=concurrent,
                draft=draft,
                task=task,
                account=account,
                idempotent_replay=True,
            )

        try:
            telegram_result = await self._telegram.send_message(
                chat_id=target_chat_id,
                text=draft.draft_text,
                business_connection_id=connection.business_connection_id,
            )
            if telegram_result.get("ok") is not True:
                raise RuntimeError("Telegram API did not confirm delivery")
        except Exception as exc:
            status, error_code = _delivery_failure(exc)
            attempt.status = status
            attempt.error_code = error_code
            attempt.error_detail = _safe_error_detail(exc)
            attempt.finished_at = datetime.now(UTC)
            self._session.add(
                AuditLog(
                    actor=f"mcp:{command.principal.subject}"[:255],
                    action=f"reply_send_{status}_from_mcp",
                    bot_id=conversation.bot_id,
                    account_id=draft.account_id,
                    project_id=draft.project_id,
                    metadata_={
                        "attempt_id": str(attempt.id),
                        "draft_id": str(draft.id),
                        "task_id": str(task.id),
                        "account_label": label,
                        "target_chat_id": target_chat_id,
                        "business_connection_id": connection.business_connection_id,
                        "error_code": error_code,
                    },
                )
            )
            await self._session.commit()
            return self._attempt_result(
                attempt=attempt,
                draft=draft,
                task=task,
                account=account,
            )

        finished_at = datetime.now(UTC)
        attempt.status = SendAttemptStatus.SENT.value
        attempt.provider_message_id = _provider_message_id(telegram_result)
        attempt.error_code = None
        attempt.error_detail = None
        attempt.finished_at = finished_at
        draft.status = ReplyDraftStatus.APPROVED.value
        task.status = ReplyTaskStatus.REPLIED.value
        task.due_at = None
        conversation.status = ConversationStatus.REPLIED.value
        conversation.requires_reply = False
        conversation.last_outbound_at = finished_at
        conversation.last_message_at = finished_at
        await self._session.execute(
            update(ReplyTask)
            .where(
                ReplyTask.conversation_id == conversation.id,
                ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            )
            .values(
                status=ReplyTaskStatus.REPLIED.value,
                due_at=None,
                updated_at=finished_at,
            )
        )
        self._session.add(
            Message(
                bot_id=conversation.bot_id,
                gate_event_id=None,
                account_id=draft.account_id,
                project_id=draft.project_id,
                direction=MessageDirection.OUTBOUND.value,
                source="gate-approved-draft",
                event_type="telegram_business_reply",
                chat_id=target_chat_id,
                sender_id=(
                    str(account.telegram_user_id)
                    if account.telegram_user_id is not None
                    else "gate"
                ),
                recipient_id=target_chat_id,
                text=draft.draft_text,
                business_connection_id=connection.business_connection_id,
                payload=telegram_result,
                sent_at=finished_at,
            )
        )
        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action="reply_send_sent_from_mcp",
                bot_id=conversation.bot_id,
                account_id=draft.account_id,
                project_id=draft.project_id,
                metadata_={
                    "attempt_id": str(attempt.id),
                    "draft_id": str(draft.id),
                    "task_id": str(task.id),
                    "account_label": label,
                    "target_chat_id": target_chat_id,
                    "business_connection_id": connection.business_connection_id,
                    "provider_message_id": attempt.provider_message_id,
                },
            )
        )
        await self._session.commit()
        return self._attempt_result(
            attempt=attempt,
            draft=draft,
            task=task,
            account=account,
        )

    async def _load_context(
        self,
        command: SendApprovedReplyCommand,
    ) -> tuple[ReplyDraft, ReplyTask, Conversation, Message, Account]:
        scope = command.principal.access_scope
        result = await self._session.execute(
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
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            raise ReplySendError(
                code="draft_not_found",
                message="Reply draft not found in the allowed scope",
            )
        draft, task, conversation, trigger, account = row
        return draft, task, conversation, trigger, account

    async def _load_origin_connection(
        self,
        *,
        command: SendApprovedReplyCommand,
        draft: ReplyDraft,
        conversation: Conversation,
        trigger: Message,
    ) -> TelegramBusinessConnection:
        origin_id = conversation.business_connection_id or trigger.business_connection_id
        if not origin_id:
            raise ReplySendError(
                code="origin_connection_missing",
                message="The originating Telegram Business connection is missing",
            )
        result = await self._session.execute(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.business_connection_id == origin_id,
                TelegramBusinessConnection.account_id == draft.account_id,
                TelegramBusinessConnection.account_id.in_(
                    command.principal.access_scope.account_ids
                ),
                TelegramBusinessConnection.is_enabled.is_(True),
            )
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise ReplySendError(
                code="origin_connection_unavailable",
                message="The originating Telegram Business connection is not active",
            )
        return connection

    async def _load_attempt(self, draft_id: Any) -> SendAttempt | None:
        result = await self._session.execute(
            select(SendAttempt)
            .where(SendAttempt.reply_draft_id == draft_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

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

    def _validate_delivery(
        self,
        *,
        draft: ReplyDraft,
        task: ReplyTask,
        account: Account,
        account_label_value: str,
        connection: TelegramBusinessConnection,
    ) -> None:
        if draft.status not in {
            ReplyDraftStatus.GENERATED.value,
            ReplyDraftStatus.EDITED.value,
        }:
            raise ReplySendError(
                code="draft_closed",
                message="Reply draft is not available for sending",
            )
        if task.status != ReplyTaskStatus.NEEDS_REPLY.value:
            raise ReplySendError(
                code="task_closed",
                message="Reply task no longer needs a reply",
            )
        if not account.is_active or not account.send_enabled:
            raise ReplySendError(
                code="account_send_disabled",
                message=f"Sending is disabled for account {account_label_value}",
            )
        if not _runtime_send_enabled(
            settings=self._settings,
            account_label_value=account_label_value,
        ):
            raise ReplySendError(
                code="runtime_send_disabled",
                message=f"Runtime sending is disabled for account {account_label_value}",
            )
        if connection.rights.get("can_reply") is False:
            raise ReplySendError(
                code="connection_cannot_reply",
                message=f"Telegram connection for {account_label_value} cannot reply",
            )

    def _attempt_result(
        self,
        *,
        attempt: SendAttempt,
        draft: ReplyDraft,
        task: ReplyTask,
        account: Account,
        idempotent_replay: bool = False,
    ) -> ReplySendResult:
        return ReplySendResult(
            attempt_id=attempt.id,
            draft_id=draft.id,
            task_id=task.id,
            account_id=draft.account_id,
            project_id=draft.project_id,
            account_alias=account.alias,
            account_label=attempt.account_label,
            recipient_chat_id=attempt.target_chat_id,
            status=attempt.status,
            sent=attempt.status == SendAttemptStatus.SENT.value,
            idempotent_replay=idempotent_replay,
            provider_message_id=attempt.provider_message_id,
            error_code=attempt.error_code,
        )


def _runtime_send_enabled(*, settings: Settings, account_label_value: str) -> bool:
    if not settings.gate_send_enabled:
        return False
    if account_label_value == "RU":
        return settings.gate_account_ru_send_enabled
    if account_label_value == "BY":
        return settings.gate_account_by_send_enabled
    return False


def _delivery_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return SendAttemptStatus.UNKNOWN.value, "telegram_timeout"
    return SendAttemptStatus.FAILED.value, "telegram_error"


def _safe_error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"telegram_http_{exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "telegram_timeout"
    return exc.__class__.__name__[:128]


def _provider_message_id(result: dict[str, Any]) -> str | None:
    payload = result.get("result")
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("message_id")
    return str(message_id) if message_id is not None else None
