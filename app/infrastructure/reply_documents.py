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
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
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
        # Clear expired bytes during every staging operation. A scheduled
        # cleanup must also call prune_expired_reply_documents independently.
        await prune_expired_reply_documents(self._session)
        draft = await SaveReplyDraftService(
            SqlReplyDraftWriter(self._session)
        ).execute(
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
        model.prompt_context = {
            **model.prompt_context,
            "kind": "document",
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
