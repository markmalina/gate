"""Default-off PostgreSQL fence shared by Business and personal sends.

It can reserve an existing Gate draft or one standalone private-user command.
It never loads a Telegram session or calls the provider.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.personal_telegram_fence import (
    PersonalSendFenceCommand,
    PersonalSendFenceResult,
    PhysicalTelegramPeer,
)
from app.application.personal_telegram_transport import (
    _digest,
    _require_prefixed_ref,
)
from app.models.domain import (
    Account,
    Conversation,
    PersonalTelegramSendFence,
    ReplyDraft,
    ReplyDraftStatus,
    SendAttempt,
    SendAttemptStatus,
)

_ACTIVE_BUSINESS = (
    SendAttemptStatus.RESERVED.value,
    SendAttemptStatus.SENDING.value,
    SendAttemptStatus.UNKNOWN.value,
)
_HEX = frozenset("0123456789abcdef")


class PersonalSendFenceError(RuntimeError):
    """Stable redacted refusal from a shared delivery reservation."""


class SqlPersonalTelegramSendFence:
    """Commit an ambiguous reservation under a shared Business lock."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, enabled: bool = False
    ) -> None:
        self._sessions = sessions
        self._enabled = enabled

    async def reserve(self, command: PersonalSendFenceCommand) -> PersonalSendFenceResult:
        if not self._enabled:
            raise PersonalSendFenceError("personal_telegram_shared_fence_disabled")
        try:
            physical_peer = PhysicalTelegramPeer.from_key(command.physical_peer_key)
        except ValueError as exc:
            raise PersonalSendFenceError(str(exc)) from None
        if command.draft_id is None and physical_peer.kind != "user":
            # Business currently exposes a typed physical identity only for its
            # private user dialogs.  Do not pretend group/channel admission is
            # cross-transport until that metadata is available.
            raise PersonalSendFenceError("personal_telegram_physical_peer_unsupported")
        fingerprint = _fingerprint(command)
        async with self._sessions.begin() as session:
            conversation_id = None
            conversation = None
            if command.draft_id is not None:
                # Use the same conversation-first lock order as Business.
                conversation_id = await session.scalar(
                    select(ReplyDraft.conversation_id).where(ReplyDraft.id == command.draft_id)
                )
                if conversation_id is None:
                    raise PersonalSendFenceError("personal_telegram_draft_not_found")
                conversation = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.account_id == command.account_id,
                    )
                    .with_for_update()
                )
                if conversation is None:
                    raise PersonalSendFenceError("personal_telegram_account_mismatch")

            # Standalone reservations serialize on the account row. Business
            # locks the same row before its final physical-peer recheck.
            account = await session.scalar(
                select(Account).where(Account.id == command.account_id).with_for_update()
            )

            existing_filter = [PersonalTelegramSendFence.idempotency_key == command.idempotency_key]
            if command.draft_id is not None:
                existing_filter.append(PersonalTelegramSendFence.reply_draft_id == command.draft_id)
            existing = await session.scalar(
                select(PersonalTelegramSendFence).where(or_(*existing_filter)).limit(1)
            )
            if existing is not None:
                legacy = (
                    existing.physical_peer_key == physical_peer.key
                    and existing.fingerprint == _legacy_draft_fingerprint(command)
                )
                if existing.fingerprint != fingerprint and not legacy:
                    raise PersonalSendFenceError("personal_telegram_fence_conflict")
                return _result(existing, replay=True)

            if (
                account is None
                or account.owner.casefold() not in ("mark", "mark-personal")
                or not account.is_active
                or not account.send_enabled
            ):
                raise PersonalSendFenceError("personal_telegram_account_unavailable")

            if command.draft_id is not None:
                draft = await session.scalar(
                    select(ReplyDraft)
                    .where(
                        ReplyDraft.id == command.draft_id,
                        ReplyDraft.conversation_id == conversation_id,
                        ReplyDraft.account_id == command.account_id,
                    )
                    .with_for_update()
                )
                if draft is None or draft.status not in (
                    ReplyDraftStatus.GENERATED.value,
                    ReplyDraftStatus.EDITED.value,
                ):
                    raise PersonalSendFenceError("personal_telegram_draft_unavailable")
                if (
                    hashlib.sha256(draft.draft_text.encode("utf-8")).hexdigest()
                    != command.exact_text_digest
                ):
                    raise PersonalSendFenceError("personal_telegram_exact_text_mismatch")
                if (
                    conversation is None
                    or physical_peer.kind != "user"
                    or str(physical_peer.provider_id) != conversation.chat_id
                ):
                    raise PersonalSendFenceError("personal_telegram_physical_peer_mismatch")

            business_filter = [
                and_(
                    SendAttempt.account_id == command.account_id,
                    SendAttempt.target_chat_id == str(physical_peer.provider_id),
                    SendAttempt.status.in_(_ACTIVE_BUSINESS),
                )
            ]
            if command.draft_id is not None:
                business_filter.append(SendAttempt.reply_draft_id == command.draft_id)
            blocked = await session.scalar(
                select(SendAttempt.id).where(or_(*business_filter)).limit(1)
            )
            if blocked is not None:
                raise PersonalSendFenceError("personal_telegram_business_attempt_exists")
            other_personal = await session.scalar(
                select(PersonalTelegramSendFence.id)
                .where(
                    PersonalTelegramSendFence.account_id == command.account_id,
                    PersonalTelegramSendFence.physical_peer_key == physical_peer.key,
                    PersonalTelegramSendFence.status == "unknown",
                )
                .limit(1)
            )
            if other_personal is not None:
                raise PersonalSendFenceError("personal_telegram_personal_attempt_exists")

            fence = PersonalTelegramSendFence(
                id=uuid4(),
                reply_draft_id=command.draft_id,
                conversation_id=conversation_id,
                account_id=command.account_id,
                physical_peer_key=physical_peer.key,
                idempotency_key=command.idempotency_key,
                actor_ref=command.actor_ref,
                session_ref=command.session_ref,
                peer_ref=command.peer_ref,
                text_digest=command.exact_text_digest,
                approval_digest=command.approval_digest,
                fingerprint=fingerprint,
                status="unknown",
                reserved_at=command.now,
            )
            session.add(fence)
            await session.flush()
            return _result(fence, replay=False)

    async def finalize(
        self,
        command: PersonalSendFenceCommand,
        *,
        outcome: str,
        receipt_ref: str | None,
        external_effect_performed: bool | None,
    ) -> PersonalSendFenceResult:
        if not self._enabled:
            raise PersonalSendFenceError("personal_telegram_shared_fence_disabled")
        fingerprint = _fingerprint(command)
        if outcome == "sent":
            if external_effect_performed is not True or not _valid_receipt_ref(receipt_ref):
                outcome, receipt_ref = "unknown", None
        elif outcome == "rejected":
            if external_effect_performed is not False or receipt_ref is not None:
                outcome, receipt_ref = "unknown", None
        else:
            outcome, receipt_ref = "unknown", None
        async with self._sessions.begin() as session:
            fence = await session.scalar(
                select(PersonalTelegramSendFence)
                .where(PersonalTelegramSendFence.idempotency_key == command.idempotency_key)
                .with_for_update()
            )
            legacy = (
                fence is not None
                and fence.physical_peer_key == command.physical_peer_key
                and fence.fingerprint == _legacy_draft_fingerprint(command)
            )
            if fence is None or (fence.fingerprint != fingerprint and not legacy):
                raise PersonalSendFenceError("personal_telegram_fence_conflict")
            if fence.status != "unknown" or fence.finished_at is not None:
                return _result(fence, replay=True)
            fence.status = outcome
            fence.provider_receipt_ref = receipt_ref
            fence.finished_at = datetime.now(UTC)
            return _result(fence, replay=False)


def _fingerprint(command: PersonalSendFenceCommand) -> str:
    for value, prefix in (
        (command.actor_ref, "actor_"),
        (command.session_ref, "session_"),
        (command.peer_ref, "peer_"),
        (command.idempotency_key, "send_"),
    ):
        _require_prefixed_ref(value, prefix, prefix.removesuffix("_"))
    if command.now.tzinfo is None:
        raise PersonalSendFenceError("personal_telegram_time_invalid")
    if not _valid_digest(command.exact_text_digest) or not _valid_digest(command.approval_digest):
        raise PersonalSendFenceError("personal_telegram_digest_invalid")
    try:
        physical_peer_key = PhysicalTelegramPeer.from_key(command.physical_peer_key).key
    except ValueError as exc:
        raise PersonalSendFenceError(str(exc)) from None
    return _digest(
        {
            "draft_id": str(command.draft_id),
            "account_id": str(command.account_id),
            "physical_peer_key": physical_peer_key,
            "actor_ref": command.actor_ref,
            "session_ref": command.session_ref,
            "peer_ref": command.peer_ref,
            "exact_text_digest": command.exact_text_digest,
            "approval_digest": command.approval_digest,
            "idempotency_key": command.idempotency_key,
        }
    )


def _legacy_draft_fingerprint(command: PersonalSendFenceCommand) -> str:
    """Accept replay/finalization of a pre-0126 draft reservation."""

    if command.draft_id is None:
        return ""
    return _digest(
        {
            "draft_id": str(command.draft_id),
            "account_id": str(command.account_id),
            "actor_ref": command.actor_ref,
            "session_ref": command.session_ref,
            "peer_ref": command.peer_ref,
            "exact_text_digest": command.exact_text_digest,
            "approval_digest": command.approval_digest,
            "idempotency_key": command.idempotency_key,
        }
    )


def _valid_digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)


def _valid_receipt_ref(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith("telegram_") and value[9:].isdigit()


def _result(fence: PersonalTelegramSendFence, *, replay: bool) -> PersonalSendFenceResult:
    return PersonalSendFenceResult(
        fence_id=fence.id,
        outcome=fence.status,
        receipt_ref=fence.provider_receipt_ref,
        replay=replay,
    )
