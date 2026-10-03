"""Scoped SQL staging of one immutable PDF or ZIP for a reply task."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.reply_documents import (
    PreparedReplyDocument,
    PrepareReplyDocumentCommand,
)
from app.application.reply_drafts import (
    ReplyDraftError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.telegram_formatting import CAPTION_FORMAT_KEY, FORMAT_KEY
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.models.domain import ReplyDocument, ReplyDraft


class SqlReplyDocumentWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        command: PrepareReplyDocumentCommand,
        data: bytes,
    ) -> PreparedReplyDocument:
        if command.formatting is None or command.formatting.text != command.caption:
            raise ReplyDraftError(
                code="invalid_formatting",
                message="A document caption requires its exact prepared formatting snapshot",
            )
        # Clear expired bytes during every staging operation. A scheduled
        # cleanup must also call prune_expired_reply_documents independently.
        await prune_expired_reply_documents(self._session)
        draft = await SaveReplyDraftService(SqlReplyDraftWriter(self._session)).execute(
            SaveReplyDraftCommand(
                principal=command.principal,
                task_id=command.task_id,
                text=command.filename,
                follow_up=command.follow_up,
            )
        )
        model = await self._session.scalar(
            select(ReplyDraft).where(ReplyDraft.id == draft.draft_id).with_for_update()
        )
        if model is None:
            raise RuntimeError("Prepared reply draft disappeared")
        model.source = "chatgpt-mcp-document"
        context = dict(model.prompt_context)
        # Filename is the legacy draft placeholder, not a text-send preview.
        # The caption is the sole formatting authority for a document draft.
        context.pop(FORMAT_KEY, None)
        model.prompt_context = {
            **context,
            "kind": "document",
            CAPTION_FORMAT_KEY: command.formatting.to_dict(),
        }
        self._session.add(
            ReplyDocument(
                reply_draft_id=draft.draft_id,
                principal_subject=command.principal.subject[:255],
                filename=command.filename,
                media_type=command.media_type,
                size_bytes=len(data),
                sha256=command.expected_sha256,
                caption=command.caption,
                data=data,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await self._session.flush()
        return PreparedReplyDocument(
            draft_id=draft.draft_id,
            task_id=draft.task_id,
            account_label=draft.account_label,
            recipient_chat_id=draft.recipient_chat_id,
            filename=command.filename,
            media_type=command.media_type,
            size_bytes=len(data),
            sha256=command.expected_sha256,
            caption=command.caption,
            formatting=command.formatting,
        )


async def prune_expired_reply_documents(session: AsyncSession) -> int:
    """Erase bytes after the preparation window while retaining receipt metadata."""

    result = await session.execute(
        update(ReplyDocument)
        .where(
            ReplyDocument.expires_at <= datetime.now(UTC),
            ReplyDocument.data.is_not(None),
        )
        .values(data=None)
        .returning(ReplyDocument.id)
    )
    return len(result.scalars().all())
