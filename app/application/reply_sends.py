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
    expected_document_sha256: str | None = None
    expected_document_filename: str | None = None
    expected_document_size_bytes: int | None = None
    expected_document_caption: str | None = None


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
    error_detail: str | None = None
    document_filename: str | None = None
    document_media_type: str | None = None
    document_size_bytes: int | None = None
    document_sha256: str | None = None


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
        if expected_account not in {"RU", "BY"}:
            raise ReplySendError(
                code="invalid_confirmation",
                message="Expected account must be RU or BY",
            )
        recipient = command.expected_recipient_chat_id.strip()
        if not recipient:
            raise ReplySendError(
                code="invalid_confirmation",
                message="Expected recipient chat id is required",
            )
        if command.expected_document_sha256 is not None:
            if (
                len(command.expected_document_sha256) != 64
                or any(char not in "0123456789abcdef" for char in command.expected_document_sha256)
                or not command.expected_document_filename
                or type(command.expected_document_size_bytes) is not int
                or command.expected_document_size_bytes <= 0
                or command.expected_document_caption is None
            ):
                raise ReplySendError(
                    code="invalid_confirmation",
                    message="Exact document metadata confirmation is required",
                )
        elif any(
            value is not None
            for value in (
                command.expected_document_filename,
                command.expected_document_size_bytes,
                command.expected_document_caption,
            )
        ):
            raise ReplySendError(
                code="invalid_confirmation",
                message="Incomplete document confirmation",
            )
        return await self._executor.send(
            SendApprovedReplyCommand(
                principal=principal,
                draft_id=command.draft_id,
                confirmed=True,
                expected_account=expected_account,
                expected_recipient_chat_id=recipient,
                expected_document_sha256=command.expected_document_sha256,
                expected_document_filename=command.expected_document_filename,
                expected_document_size_bytes=command.expected_document_size_bytes,
                expected_document_caption=command.expected_document_caption,
            )
        )
