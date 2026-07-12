"""Application contract for an explicit, scoped and idempotent reply send."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.mcp_security import Principal

SEND_MESSAGE_SCOPE = "gate:messages:send"


class ReplySendError(Exception):
    """Expected send error safe to expose without secrets or message leakage."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SendApprovedReplyCommand:
    """One explicitly confirmed send of an already persisted draft."""

    principal: Principal
    draft_id: UUID
    confirmed: bool
    expected_account: str
    expected_recipient_chat_id: str


@dataclass(frozen=True, slots=True)
class ReplySendResult:
    """Bounded delivery outcome returned to the MCP adapter."""

    attempt_id: UUID
    draft_id: UUID
    task_id: UUID
    account_id: UUID
    project_id: UUID
    account_alias: str
    account_label: str
    recipient_chat_id: str
    status: str
    sent: bool
    idempotent_replay: bool
    provider_message_id: str | None = None
    error_code: str | None = None


class ReplySendExecutor(Protocol):
    """Port that reserves, delivers and records one reply send."""

    async def send(self, command: SendApprovedReplyCommand) -> ReplySendResult: ...


class SendApprovedReplyService:
    """Validate owner intent before allowing any open-world delivery attempt."""

    def __init__(self, executor: ReplySendExecutor) -> None:
        self._executor = executor

    async def execute(self, command: SendApprovedReplyCommand) -> ReplySendResult:
        principal = command.principal
        if SEND_MESSAGE_SCOPE not in principal.scopes:
            raise ReplySendError(
                code="insufficient_scope",
                message=f"Scope {SEND_MESSAGE_SCOPE} is required",
            )
        if not principal.access_scope.is_explicit:
            raise ReplySendError(
                code="invalid_access_scope",
                message="Explicit workspace, account, and project grants are required",
            )
        if command.confirmed is not True:
            raise ReplySendError(
                code="confirmation_required",
                message="Explicit confirmation is required before sending",
            )
        expected_account = command.expected_account.strip().upper()
        if not expected_account:
            raise ReplySendError(
                code="invalid_confirmation",
                message="Expected RU/BY account label is required",
            )
        recipient = command.expected_recipient_chat_id.strip()
        if not recipient:
            raise ReplySendError(
                code="invalid_confirmation",
                message="Expected recipient chat id is required",
            )
        return await self._executor.send(
            SendApprovedReplyCommand(
                principal=principal,
                draft_id=command.draft_id,
                confirmed=True,
                expected_account=expected_account,
                expected_recipient_chat_id=recipient,
            )
        )
