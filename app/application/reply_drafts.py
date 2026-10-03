"""Application contract for saving a reply draft without sending it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.telegram_formatting import FormattingError, FormattingPreview, compile_text
from app.core.mcp_security import Principal

SAVE_DRAFT_SCOPE = "gate:drafts:write"
MAX_DRAFT_LENGTH = 4096


class ReplyDraftError(Exception):
    """Expected application error safe to return from a tool call."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ReplyTaskNotFoundError(ReplyDraftError):
    """The task does not exist inside the principal's explicit grants."""

    def __init__(self) -> None:
        super().__init__(code="task_not_found", message="Reply task not found in the allowed scope")


class ReplyTaskClosedError(ReplyDraftError):
    """The task no longer accepts draft revisions."""

    def __init__(self) -> None:
        super().__init__(
            code="task_closed",
            message="Reply task is not available for a draft",
        )


class ReplyTaskSupersededError(ReplyDraftError):
    """A newer inbound task must be reviewed before any follow-up."""

    def __init__(self) -> None:
        super().__init__(
            code="task_superseded",
            message="A newer inbound reply task supersedes this follow-up",
        )


@dataclass(frozen=True, slots=True)
class SaveReplyDraftCommand:
    """A scoped request to persist text as a draft only."""

    principal: Principal
    task_id: UUID
    text: str
    follow_up: bool = False
    text_format: str = "plain"
    formatting: FormattingPreview | None = None


@dataclass(frozen=True, slots=True)
class SavedReplyDraft:
    """Safe result returned after a draft is persisted."""

    draft_id: UUID
    task_id: UUID
    account_id: UUID
    project_id: UUID
    account_alias: str
    account_label: str
    recipient_chat_id: str
    status: str
    text: str
    formatting: FormattingPreview | None = None


class ReplyDraftWriter(Protocol):
    """Persistence port used by the draft application service."""

    async def save(self, command: SaveReplyDraftCommand) -> SavedReplyDraft: ...


class SaveReplyDraftService:
    """Validate draft intent and delegate persistence without external delivery."""

    def __init__(self, writer: ReplyDraftWriter) -> None:
        self._writer = writer

    async def execute(self, command: SaveReplyDraftCommand) -> SavedReplyDraft:
        principal = command.principal
        if SAVE_DRAFT_SCOPE not in principal.scopes:
            raise ReplyDraftError(
                code="insufficient_scope",
                message=f"Scope {SAVE_DRAFT_SCOPE} is required",
            )
        if not principal.access_scope.is_explicit:
            raise ReplyDraftError(
                code="invalid_access_scope",
                message="Explicit workspace, account, and project grants are required",
            )
        try:
            preview = compile_text(command.text, text_format=command.text_format)
        except FormattingError as exc:
            raise ReplyDraftError(code=exc.code, message=exc.message) from exc
        return await self._writer.save(
            SaveReplyDraftCommand(
                principal=principal,
                task_id=command.task_id,
                text=preview.text,
                follow_up=command.follow_up,
                text_format=preview.text_format,
                formatting=preview,
            )
        )
