"""Dispatch one pre-authorized Saved Messages operation in the verified RU worker."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.personal_telegram_fence import PersonalSendFenceCommand, PersonalSendFencePort
from app.application.personal_telegram_transport import (
    MAX_EXACT_TEXT_LENGTH,
    ProviderOutcome,
    ProviderSendReceipt,
)
from app.infrastructure.personal_telegram_fence import PersonalSendFenceError
from app.personal_telegram.command_scope import CommandRuntimeError, PeerIdentity
from app.personal_telegram.command_store import LeaseToken, OperationStatus, SqlCommandStore

SEND_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class SavedMessagesApproval:
    """Supplied by a trusted caller for one journaled operation, never by Work."""

    operation_id: UUID
    delegation_id: UUID
    text_sha256: str
    expires_at: datetime
    approval_ref: str
    source_revision: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation_id, UUID)
            or not isinstance(self.delegation_id, UUID)
            or not isinstance(self.text_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.text_sha256) is None
            or not isinstance(self.expires_at, datetime)
            or self.expires_at.utcoffset() is None
            or not isinstance(self.approval_ref, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.approval_ref) is None
            or not isinstance(self.source_revision, str)
            or re.fullmatch(r"[a-f0-9]{40}", self.source_revision) is None
        ):
            raise CommandRuntimeError("saved_messages_approval_invalid")


@dataclass(frozen=True, slots=True)
class SavedMessagesDispatcher:
    approval: SavedMessagesApproval
    fence: PersonalSendFencePort

    async def dispatch_once(
        self,
        store: SqlCommandStore,
        token: LeaseToken,
        *,
        verified_owner_id: int,
        send: Callable[[str, int], Awaitable[ProviderSendReceipt]],
        clock: Callable[[], datetime],
        stop: asyncio.Event,
    ) -> OperationStatus | None:
        now = clock()
        if stop.is_set() or now >= self.approval.expires_at:
            return None
        command = await store.claim(token, now=now, operation_id=self.approval.operation_id)
        if command is None:
            return None
        text = command.payload.get("text")
        target = PeerIdentity("self", verified_owner_id)
        if (
            command.capability != "send_text"
            or command.peer_key != target.key
            or command.delegation_id != self.approval.delegation_id
            or set(command.payload) != {"text"}
            or not isinstance(text, str)
            or not 1 <= len(text) <= MAX_EXACT_TEXT_LENGTH
            or _sha256(text) != self.approval.text_sha256
        ):
            return await store.finish(
                command,
                token,
                state="rejected",
                now=clock(),
                error_code="saved_messages_scope_denied",
            )

        fence_command = PersonalSendFenceCommand(
            draft_id=None,
            account_id=store.scope.account_id,
            physical_peer_key=f"user:{verified_owner_id}",
            actor_ref="actor_" + _sha256(store.scope.owner_subject),
            session_ref="session_" + store.scope.binding_id.hex,
            peer_ref="peer_" + _sha256(target.key),
            exact_text_digest=self.approval.text_sha256,
            approval_digest=_sha256(
                f"{command.operation_id.hex}:{command.delegation_id.hex}:"
                f"{self.approval.expires_at.astimezone(UTC).isoformat()}:"
                f"{self.approval.approval_ref}:{self.approval.source_revision}"
            ),
            idempotency_key="send_" + command.operation_id.hex,
            now=now,
        )
        try:
            reservation = await self.fence.reserve(fence_command)
        except PersonalSendFenceError:
            return await store.finish(
                command,
                token,
                state="rejected",
                now=clock(),
                error_code="delivery_admission_refused",
            )
        if reservation.replay or reservation.outcome != "unknown":
            return await store.finish(
                command,
                token,
                state="unknown",
                now=clock(),
                error_code="delivery_reservation_conflict",
            )

        denial: str | None = None
        try:
            deadline = await store.require_dispatch(command, token, now=clock())
            remaining = (min(deadline, self.approval.expires_at) - clock()).total_seconds()
            if stop.is_set() or remaining <= 0:
                raise CommandRuntimeError("saved_messages_approval_inactive")
        except CommandRuntimeError as exc:
            provider = ProviderSendReceipt(ProviderOutcome.REJECTED, None, False)
            denial = str(exc)
        else:
            try:
                async with asyncio.timeout(min(remaining, SEND_TIMEOUT_SECONDS)):
                    provider = await send(text, command.random_id)
            except Exception:
                provider = ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)

        try:
            receipt = await self.fence.finalize(
                fence_command,
                outcome=provider.outcome.value,
                receipt_ref=provider.provider_receipt_ref,
                external_effect_performed=provider.external_effect_performed,
            )
        except Exception:
            receipt = None
        receipt_ref = provider.provider_receipt_ref
        if (
            receipt is not None
            and provider.outcome == ProviderOutcome.SENT
            and provider.external_effect_performed is True
            and receipt.outcome == "sent"
            and receipt.receipt_ref == receipt_ref
            and receipt_ref is not None
            and re.fullmatch(r"telegram_[1-9][0-9]{0,18}", receipt_ref)
        ):
            return await store.finish(
                command, token, state="succeeded", now=clock(), receipt_ref=receipt_ref
            )
        if (
            receipt is not None
            and provider.outcome == ProviderOutcome.REJECTED
            and provider.external_effect_performed is False
            and provider.provider_receipt_ref is None
            and receipt.outcome == "rejected"
            and receipt.receipt_ref is None
        ):
            return await store.finish(
                command,
                token,
                state="rejected",
                now=clock(),
                error_code=denial or "delivery_rejected",
            )
        return await store.finish(
            command, token, state="unknown", now=clock(), error_code="delivery_outcome_unknown"
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
