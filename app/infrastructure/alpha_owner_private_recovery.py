"""One-shot, DB-bound recovery for the owner's private AlphaViaBot batch."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_delivery import (
    AlphaProviderDeliveryError,
    AlphaTelegramTextPort,
)
from app.application.alpha_group import AgentSendAttemptState
from app.models.domain import (
    Account,
    Message,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    SendAttempt,
    SendAttemptStatus,
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)

JsonObject = dict[str, Any]

OWNER_ACCEPTANCE_TASK_ID = uuid.UUID("4f060e00-0bb5-4701-8f38-80b06da9fc8e")
OWNER_BATCH_DRAFT_IDS = (
    uuid.UUID("34d741d2-fad8-47c9-923b-dd83e15c3b03"),
    uuid.UUID("e4599039-6c1b-4e9a-93ee-aa3745fee7d7"),
    uuid.UUID("7e886464-1759-4693-9d16-4c08c900f32d"),
    uuid.UUID("a749cefa-ffcf-4eb2-830d-8de7f66616a6"),
)
_RECOVERY_SUBJECT = "alpha-owner-private-recovery-410"
_RECOVERY_CONNECTION = "alpha-bot-owner-private"


class AlphaOwnerPrivateRecoveryError(RuntimeError):
    """Payload-free refusal for the bounded owner recovery."""

    def __init__(self, code: str) -> None:
        self.code = code[:128]
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class AlphaOwnerPrivateRecoveryResult:
    target_chat_id: str
    sent: int
    already_terminal: int
    failed: int


@dataclass(frozen=True, slots=True)
class _Reservation:
    attempt_id: uuid.UUID
    text: str


def owner_private_sender(update_payload: JsonObject) -> str | None:
    """Return one self-addressed human private sender or fail closed."""

    update_kinds = {key for key in update_payload if key != "update_id"}
    if update_kinds != {"message"}:
        return None
    message = update_payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if (
        chat.get("type") != "private"
        or type(chat_id) is not int
        or type(sender_id) is not int
        or chat_id != sender_id
        or sender.get("is_bot") is not False
    ):
        return None
    return str(chat_id)


async def authorize_owner_private_update(
    session: AsyncSession,
    update_payload: JsonObject,
) -> str | None:
    """Resolve the owner through an existing content-free accepted Gate task."""

    sender_id = owner_private_sender(update_payload)
    if sender_id is None:
        return None
    rows = (
        await session.scalars(
            select(Message.sender_id)
            .join(ReplyTask, ReplyTask.trigger_message_id == Message.id)
            .join(Account, Account.id == ReplyTask.account_id)
            .where(
                ReplyTask.id == OWNER_ACCEPTANCE_TASK_ID,
                ReplyTask.account_id == TELEGRAM_MARK_BY_ACCOUNT_ID,
                Message.account_id == TELEGRAM_MARK_BY_ACCOUNT_ID,
                Message.direction == "inbound",
                Message.sender_id == sender_id,
                Message.chat_id == sender_id,
                Account.id == TELEGRAM_MARK_BY_ACCOUNT_ID,
                Account.is_active.is_(True),
                Account.send_enabled.is_(True),
            )
        )
    ).all()
    return sender_id if rows == [sender_id] else None


class SqlAlphaOwnerPrivateRecovery:
    """Deliver the four exact DB-held drafts with per-draft terminal idempotency."""

    def __init__(self, session: AsyncSession, telegram: AlphaTelegramTextPort) -> None:
        self._session = session
        self._telegram = telegram

    async def execute(
        self,
        *,
        target_chat_id: str,
        now: datetime,
    ) -> AlphaOwnerPrivateRecoveryResult:
        await self._preflight(target_chat_id=target_chat_id)
        sent = 0
        already_terminal = 0
        failed = 0
        for draft_id in OWNER_BATCH_DRAFT_IDS:
            reservation = await self._reserve(
                draft_id=draft_id,
                target_chat_id=target_chat_id,
                now=now,
            )
            if reservation is None:
                already_terminal += 1
                continue
            try:
                provider_message_id = await self._telegram.send_text(
                    chat_id=target_chat_id,
                    text=reservation.text,
                )
            except AlphaProviderDeliveryError as exc:
                state = (
                    SendAttemptStatus.UNKNOWN.value
                    if exc.state is AgentSendAttemptState.UNKNOWN
                    else SendAttemptStatus.FAILED.value
                )
                await self._finalize(
                    reservation.attempt_id,
                    state=state,
                    now=now,
                    error_code=exc.error_code,
                )
                failed += 1
                continue
            except Exception:
                await self._finalize(
                    reservation.attempt_id,
                    state=SendAttemptStatus.UNKNOWN.value,
                    now=now,
                    error_code="alpha_owner_private_outcome_unknown",
                )
                failed += 1
                continue
            await self._finalize(
                reservation.attempt_id,
                state=SendAttemptStatus.SENT.value,
                now=now,
                provider_message_id=provider_message_id,
            )
            sent += 1
        return AlphaOwnerPrivateRecoveryResult(
            target_chat_id=target_chat_id,
            sent=sent,
            already_terminal=already_terminal,
            failed=failed,
        )

    async def complete_receipt(
        self,
        *,
        receipt_id: str,
        outbox_event_id: str,
        now: datetime,
    ) -> None:
        try:
            receipt_uuid = uuid.UUID(receipt_id)
            event_uuid = uuid.UUID(outbox_event_id)
        except ValueError as exc:
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_receipt_identity_invalid"
            ) from exc
        await self._session.execute(
            update(TelegramReceipt)
            .where(TelegramReceipt.id == receipt_uuid)
            .values(
                status=TelegramReceiptStatus.PROCESSED.value,
                processed_at=now,
                last_error_code=None,
                last_error_at=None,
            )
        )
        await self._session.execute(
            update(TelegramOutboxEvent)
            .where(TelegramOutboxEvent.id == event_uuid)
            .values(
                status=TelegramOutboxStatus.DELIVERED.value,
                delivered_at=now,
                last_error_code=None,
                last_error_at=None,
            )
        )
        await self._session.commit()

    async def _preflight(self, *, target_chat_id: str) -> None:
        rows = (
            await self._session.execute(
                select(ReplyDraft, Message)
                .join(ReplyTask, ReplyTask.id == ReplyDraft.reply_task_id)
                .join(Message, Message.id == ReplyDraft.trigger_message_id)
                .where(
                    ReplyDraft.id.in_(OWNER_BATCH_DRAFT_IDS),
                    ReplyDraft.reply_task_id == OWNER_ACCEPTANCE_TASK_ID,
                    ReplyDraft.account_id == TELEGRAM_MARK_BY_ACCOUNT_ID,
                    ReplyDraft.status == ReplyDraftStatus.GENERATED.value,
                    ReplyTask.id == OWNER_ACCEPTANCE_TASK_ID,
                    Message.sender_id == target_chat_id,
                    Message.chat_id == target_chat_id,
                )
            )
        ).all()
        if len(rows) != len(OWNER_BATCH_DRAFT_IDS):
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_batch_binding_invalid"
            )
        drafts = {draft.id: draft for draft, _message in rows}
        if set(drafts) != set(OWNER_BATCH_DRAFT_IDS):
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_batch_binding_invalid"
            )
        if any(
            not drafts[draft_id].draft_text.strip()
            or len(drafts[draft_id].draft_text) > 4_096
            for draft_id in OWNER_BATCH_DRAFT_IDS
        ):
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_batch_text_invalid"
            )

    async def _reserve(
        self,
        *,
        draft_id: uuid.UUID,
        target_chat_id: str,
        now: datetime,
    ) -> _Reservation | None:
        row = (
            await self._session.execute(
                select(ReplyDraft, SendAttempt)
                .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
                .where(
                    ReplyDraft.id == draft_id,
                    ReplyDraft.reply_task_id == OWNER_ACCEPTANCE_TASK_ID,
                    ReplyDraft.account_id == TELEGRAM_MARK_BY_ACCOUNT_ID,
                )
                .with_for_update(of=ReplyDraft)
            )
        ).one_or_none()
        if row is None:
            await self._session.rollback()
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_draft_binding_invalid"
            )
        draft, existing = row
        if existing is not None:
            await self._session.rollback()
            return None
        attempt = SendAttempt(
            reply_draft_id=draft.id,
            reply_task_id=draft.reply_task_id,
            conversation_id=draft.conversation_id,
            account_id=draft.account_id,
            project_id=draft.project_id,
            principal_subject=_RECOVERY_SUBJECT,
            status=SendAttemptStatus.SENDING.value,
            account_label="AlphaViaBot",
            target_chat_id=target_chat_id,
            business_connection_id=_RECOVERY_CONNECTION,
            reserved_at=now,
            started_at=now,
        )
        self._session.add(attempt)
        await self._session.commit()
        return _Reservation(attempt_id=attempt.id, text=draft.draft_text)

    async def _finalize(
        self,
        attempt_id: uuid.UUID,
        *,
        state: str,
        now: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        attempt = await self._session.scalar(
            select(SendAttempt)
            .where(
                SendAttempt.id == attempt_id,
                SendAttempt.status == SendAttemptStatus.SENDING.value,
            )
            .with_for_update()
        )
        if attempt is None:
            await self._session.rollback()
            raise AlphaOwnerPrivateRecoveryError(
                "alpha_owner_private_attempt_transition_invalid"
            )
        attempt.status = state
        attempt.provider_message_id = provider_message_id
        attempt.error_code = error_code
        attempt.finished_at = now
        await self._session.commit()
