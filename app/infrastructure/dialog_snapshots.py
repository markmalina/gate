"""Durable dialog revisions shared by ingress and reply-send admission."""

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog_snapshots import SNAPSHOT_KEY, snapshot_digest, snapshot_matches
from app.application.reply_sends import ReplySendError
from app.core.mcp_security import AccessScope
from app.core.telegram_connectors import TelegramConnectorKey
from app.models.domain import (
    AuditLog,
    Conversation,
    Message,
    ReplyDialogVersion,
    ReplyDraft,
    TelegramBusinessConnection,
    TelegramReceipt,
)


async def bump_dialog_version(
    session: AsyncSession, *, connection_id: str, chat_id: str
) -> None:
    """Caller commits this increment with its durable receipt or send attempt."""
    await session.execute(
        insert(ReplyDialogVersion)
        .values(business_connection_id=connection_id, chat_id=chat_id, revision=1)
        .on_conflict_do_update(
            index_elements=["business_connection_id", "chat_id"],
            set_={"revision": ReplyDialogVersion.revision + 1},
        )
    )


async def dialog_snapshot(
    session: AsyncSession, *, scope: AccessScope, conversation: Conversation,
    lock: bool = False,
) -> str:
    """Bind the fingerprint to scope, every account connection and projected facts.

    On admission, UPSERT locks the keys including previously nonexistent rows.
    Ingress uses the same rows. No SQL transaction crosses provider HTTP.
    """
    if (
        conversation.account_id not in scope.account_ids
        or conversation.project_id not in scope.project_ids
    ):
        raise ReplySendError(code="context_scope_mismatch", message="Dialog outside allowed scope")
    connections = set((await session.scalars(
        select(TelegramBusinessConnection.business_connection_id).where(
            TelegramBusinessConnection.account_id == conversation.account_id
        )
    )).all())
    if conversation.business_connection_id:
        connections.add(conversation.business_connection_id)
    # Imported/legacy history is still readable without a Business connection.
    # The sender separately requires an authorized live origin before any I/O.
    if lock:
        for connection in sorted(connections):
            await session.execute(
                insert(ReplyDialogVersion)
                .values(business_connection_id=connection, chat_id=conversation.chat_id, revision=0)
                .on_conflict_do_update(
                    index_elements=["business_connection_id", "chat_id"],
                    set_={"revision": ReplyDialogVersion.revision},
                )
            )
    revision_rows = (await session.execute(
        select(ReplyDialogVersion.business_connection_id, ReplyDialogVersion.revision).where(
            ReplyDialogVersion.business_connection_id.in_(connections),
            ReplyDialogVersion.chat_id == conversation.chat_id,
        )
    )).all()
    revisions: dict[str, int] = {key: value for key, value in revision_rows}
    # Include actual stored messages as well as raw-ingress revisions. This also
    # detects imported/manual facts, edits, removals and projection advancing
    # while a Work was composing. Operational retries do not affect the token.
    messages = (await session.execute(
        select(Message.id, Message.text, Message.direction, Message.payload,
               Message.created_at, Message.sent_at, Message.received_at).where(
            Message.account_id == conversation.account_id,
            Message.project_id.in_(scope.project_ids),
            Message.chat_id == conversation.chat_id,
        ).order_by(Message.id)
    )).all()
    # Opaque IDs detect projected changes in another routed project without
    # exposing that project's private message content to the current Work.
    message_ids = (await session.scalars(select(Message.id).where(
        Message.account_id == conversation.account_id,
        Message.chat_id == conversation.chat_id,
    ).order_by(Message.id))).all()
    return snapshot_digest({
        "workspace": scope.workspace_id,
        "account": str(conversation.account_id),
        "project": str(conversation.project_id),
        "conversation": str(conversation.id),
        "chat": conversation.chat_id,
        "connections": [(key, revisions[key]) for key in sorted(connections)
                        if revisions.get(key, 0) > 0],
        "messages": [tuple(row) for row in messages],
        "dialog_message_ids": [str(message_id) for message_id in message_ids],
    })


async def require_dialog_snapshot(
    session: AsyncSession, *, scope: AccessScope, conversation: Conversation,
    expected: object, lock: bool = False,
) -> str:
    if not isinstance(expected, str):
        raise ReplySendError(
            code="context_snapshot_required",
            message="Read current reply context and prepare a new snapshot-bound draft",
        )
    current = await dialog_snapshot(session, scope=scope, conversation=conversation, lock=lock)
    if not snapshot_matches(expected, current):
        raise ReplySendError(
            code="context_changed",
            message="Отправка остановлена: диалог изменился. Прочитайте новые сообщения "
                    "и подготовьте ответ по актуальному контексту.",
        )
    return current


async def require_saved_dialog_snapshot(
    session: AsyncSession, *, scope: AccessScope, draft: ReplyDraft,
    conversation: Conversation, lock: bool = False,
) -> None:
    context: dict[str, Any] = draft.prompt_context or {}
    expected = context.get(SNAPSHOT_KEY)
    sequence = context.get("reply_sequence")
    if isinstance(sequence, dict) and isinstance(sequence.get("position"), int):
        position = sequence["position"]
        if position > 0:
            progress = await session.scalar(select(AuditLog.metadata_).where(
                AuditLog.action == "reply_sequence_snapshot_advanced",
                AuditLog.account_id == draft.account_id,
                AuditLog.project_id == draft.project_id,
                AuditLog.metadata_["sequence_id"].as_string() == sequence.get("id"),
                AuditLog.metadata_["position"].as_integer() == position - 1,
                AuditLog.metadata_["baseline_snapshot"].as_string() == expected,
            ).limit(1))
            if not isinstance(progress, dict):
                raise ReplySendError(
                    code="context_changed", message="Sequence has no verified snapshot progression",
                )
            expected = progress.get("next_snapshot")
    await require_dialog_snapshot(
        session, scope=scope, conversation=conversation,
        expected=expected, lock=lock,
    )


async def recent_dialog_events(
    session: AsyncSession, *, scope: AccessScope, conversation: Conversation,
    owner_id: int | None, limit: int = 20,
) -> tuple[list[dict[str, Any]], bool]:
    """Expose accepted events before projection, only for an unambiguous scope.

    A connection/chat may have multiple routed projects. In that case raw text
    cannot be attributed safely; expose only the change and projection status.
    """
    connections = set((await session.scalars(
        select(TelegramBusinessConnection.business_connection_id).where(
            TelegramBusinessConnection.account_id == conversation.account_id
        )
    )).all())
    if conversation.business_connection_id:
        connections.add(conversation.business_connection_id)
    other_project = await session.scalar(select(Conversation.id).where(
        Conversation.account_id == conversation.account_id,
        Conversation.chat_id == conversation.chat_id,
        or_(Conversation.project_id.is_(None), Conversation.project_id.not_in(scope.project_ids)),
    ).limit(1))
    predicates = []
    for key in ("business_message", "edited_business_message", "deleted_business_messages"):
        raw_event = TelegramReceipt.payload[key]
        predicates.append(and_(
            raw_event["business_connection_id"].as_string().in_(connections),
            raw_event["chat"]["id"].as_string() == conversation.chat_id,
        ))
    receipts = (await session.scalars(select(TelegramReceipt).where(
        TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
        or_(*predicates),
    ).order_by(TelegramReceipt.received_at.desc(), TelegramReceipt.id.desc())
        .limit(limit + 1))).all()
    result: list[dict[str, Any]] = []
    for receipt in reversed(receipts[:limit]):
        for key in ("business_message", "edited_business_message", "deleted_business_messages"):
            event = receipt.payload.get(key)
            if isinstance(event, dict):
                sender = event.get("from")
                direction = (
                    "outbound" if isinstance(sender, dict) and sender.get("id") == owner_id
                    else "inbound" if isinstance(sender, dict) else "unknown"
                )
                value = event.get("text", event.get("caption"))
                text_value = value if isinstance(value, str) and other_project is None else None
                result.append({
                    "event_ref": str(receipt.id), "kind": key, "direction": direction,
                    "received_at": receipt.received_at.isoformat(),
                    "text": text_value[:4096] if text_value is not None else None,
                    "text_truncated": text_value is not None and len(text_value) > 4096,
                    "projection_status": receipt.status,
                    "content_available": other_project is None,
                    "content_type": next((kind for kind in (
                        "voice", "photo", "document", "video", "audio", "sticker", "text"
                    ) if kind in event), "other"),
                })
                break
    return result, len(receipts) > limit
