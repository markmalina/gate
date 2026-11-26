"""Last message IDs shared by dialog reads and reply-send admission."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Text, and_, case, cast, func, or_, select, true, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog_snapshots import (
    DIALOG_EVENT_KINDS,
    SNAPSHOT_KEY,
    snapshot_matches,
)
from app.application.reply_sends import ReplySendError
from app.core.mcp_security import AccessScope
from app.core.telegram_connectors import TelegramConnectorKey
from app.models.domain import (
    AuditLog,
    Conversation,
    Message,
    MessageDirection,
    ReplyDraft,
    TelegramBusinessConnection,
    TelegramReceipt,
    TelegramReceiptStatus,
)


# The same transaction lock orders durable Business acceptance and final admission.
# This namespace is local to PostgreSQL and does not require any stored state.
async def lock_dialog_ingress(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended("gate:business-ingress", 0)
    )))


async def mark_ingress_unavailable(
    session: AsyncSession,
    *,
    reason: str,
    connection_id: str | None = None,
) -> None:
    await lock_dialog_ingress(session)
    from app.infrastructure.reply_sequence_state import stop_sequences

    account_id = await session.scalar(select(TelegramBusinessConnection.account_id).where(
        TelegramBusinessConnection.business_connection_id == connection_id,
    )) if connection_id is not None else None
    await stop_sequences(session, account_id=account_id, reason=reason)
    session.add(AuditLog(
        actor="telegram_ingress",
        action="reply_ingress_unavailable",
        account_id=account_id,
        metadata_={"reason": reason, "connection_scoped": account_id is not None},
    ))


async def dialog_snapshot(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation: Conversation,
    lock: bool = False,
    own_messages: set[UUID] | None = None,
    own_receipts: set[str] | None = None,
) -> str | None:
    """Read the latest remaining message ID, including unprojected Business receipts."""
    if (
        conversation.account_id not in scope.account_ids
        or conversation.project_id not in scope.project_ids
    ):
        raise ReplySendError(code="context_scope_mismatch", message="Dialog outside allowed scope")
    if lock:
        await lock_dialog_ingress(session)
    connections = select(TelegramBusinessConnection.business_connection_id).where(
        TelegramBusinessConnection.account_id == conversation.account_id,
    )
    message_filter = (
        Message.account_id == conversation.account_id,
        Message.chat_id == conversation.chat_id,
        Message.id.not_in(own_messages or ()),
    )
    projected = select(Message.telegram_message_id.label("message_id")).where(
        *message_filter, Message.telegram_message_id > 0,
    )
    alternatives = []
    for kind in ("business_message", "message"):
        event = TelegramReceipt.payload[kind]
        raw_message_id = event["message_id"].as_string()
        alternatives.append((
            and_(
                _receipt_matches_dialog(event, connections=connections, conversation=conversation),
                raw_message_id.op("~")(r"^[1-9][0-9]{0,17}$"),
            ),
            cast(raw_message_id, BigInteger),
        ))
    accepted = select(case(*alternatives).label("message_id")).where(
        TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
        TelegramReceipt.id.not_in([UUID(identifier) for identifier in own_receipts or ()]),
    )
    deletion = TelegramReceipt.payload["deleted_business_messages"]
    deleted_array = deletion["message_ids"]
    deleted_values = func.jsonb_array_elements_text(case(
        (func.jsonb_typeof(deleted_array) == "array", deleted_array),
        else_=cast("[]", JSONB),
    )).table_valued("value").render_derived().lateral()
    deleted = select(deleted_values.c.value).select_from(TelegramReceipt).join(
        deleted_values, true()
    ).where(
        TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
        _receipt_matches_dialog(deletion, connections=connections, conversation=conversation),
        deleted_values.c.value.is_not(None),
    )
    candidates = union_all(projected, accepted).subquery()
    latest_id = await session.scalar(select(func.max(candidates.c.message_id)).where(
        cast(candidates.c.message_id, Text).not_in(deleted),
    ))
    if latest_id is not None:
        return str(latest_id)
    # Legacy imported messages can lack Telegram IDs. Their existing row ID
    # still identifies the last stored message without looking at its contents.
    legacy_id = await session.scalar(
        select(Message.id)
        .where(*message_filter, Message.telegram_message_id.is_(None))
        .order_by(func.coalesce(Message.sent_at, Message.received_at, Message.created_at).desc(),
                  Message.id.desc())
        .limit(1)
    )
    return str(legacy_id) if legacy_id is not None else None


def _receipt_matches_dialog(event: Any, *, connections: Any, conversation: Conversation) -> Any:
    connection_id = event["business_connection_id"].as_string()
    return and_(
        or_(connection_id.in_(connections), connection_id == conversation.business_connection_id),
        event["chat"]["id"].as_string() == conversation.chat_id,
    )


async def require_dialog_snapshot(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation: Conversation,
    expected: object,
    lock: bool = False,
    require_readable: bool = False,
    own_messages: set[UUID] | None = None,
    own_receipts: set[str] | None = None,
) -> str:
    if not isinstance(expected, str):
        raise ReplySendError(
            code="context_snapshot_required",
            message="Read current reply context and prepare a new snapshot-bound draft",
        )
    try:
        current = await dialog_snapshot(
            session, scope=scope, conversation=conversation, lock=lock,
            own_messages=own_messages, own_receipts=own_receipts,
        )
        if await known_dialog_gap(session, scope=scope, conversation=conversation):
            raise ReplySendError(code="context_unavailable", message="known_ingress_gap")
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        raise ReplySendError(
            code="context_unavailable", message="authoritative_version_unavailable",
        ) from exc
    if current is None or not snapshot_matches(expected, current):
        raise ReplySendError(
            code="context_changed",
            message="Отправка остановлена: диалог изменился. Прочитайте новые сообщения "
            "и подготовьте ответ по актуальному контексту.",
        )
    if require_readable:
        try:
            events, _, complete = await recent_dialog_events(
                session, scope=scope, conversation=conversation, owner_id=None,
            )
            issue = None if complete else "scope_incomplete"
            if issue is None:
                issue = await dialog_read_issue(
                    session, scope=scope, conversation=conversation, events=events,
                    latest_message_id=current,
                )
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise ReplySendError(
                code="context_unavailable", message="authoritative_version_unavailable",
            ) from exc
        if issue is not None:
            raise ReplySendError(code="context_unavailable", message=issue)
    return current


async def require_saved_dialog_snapshot(
    session: AsyncSession,
    *,
    scope: AccessScope,
    draft: ReplyDraft,
    conversation: Conversation,
    lock: bool = False,
    current_attempt_id: UUID | None = None,
) -> None:
    context: dict[str, Any] = draft.prompt_context or {}
    expected = context.get(SNAPSHOT_KEY)
    sequence = context.get("reply_sequence")
    own_messages, own_receipts = None, None
    if isinstance(sequence, dict):
        from app.infrastructure.reply_sequence_state import sequence_progress

        # Fence precedes reading the stop latch and receipt journal. Refresh a
        # draft loaded before a concurrent committed ingress stop.
        if lock:
            await lock_dialog_ingress(session)
            await session.refresh(draft)
        if await known_dialog_gap(session, scope=scope, conversation=conversation):
            raise ReplySendError(code="context_unavailable", message="known_ingress_gap")
        own_messages, own_receipts = await sequence_progress(
            session, draft=draft, current_attempt_id=current_attempt_id,
        )
    await require_dialog_snapshot(
        session,
        scope=scope,
        conversation=conversation,
        expected=expected,
        lock=lock,
        own_messages=own_messages, own_receipts=own_receipts,
    )


async def recent_dialog_events(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation: Conversation,
    owner_id: int | None,
    limit: int = 20,
    before: tuple[datetime, UUID] | None = None,
    receipt_id: UUID | None = None,
    text_offset: int = 0,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Expose accepted events before projection, only for an unambiguous scope.

    A connection/chat may have multiple routed projects. In that case raw text
    cannot be attributed safely; expose only the change and projection status.
    """
    connections = set(
        (
            await session.scalars(
                select(TelegramBusinessConnection.business_connection_id).where(
                    TelegramBusinessConnection.account_id == conversation.account_id
                )
            )
        ).all()
    )
    if conversation.business_connection_id:
        connections.add(conversation.business_connection_id)
    other_project = await session.scalar(
        select(Conversation.id)
        .where(
            Conversation.account_id == conversation.account_id,
            Conversation.chat_id == conversation.chat_id,
            or_(
                Conversation.project_id.is_(None), Conversation.project_id.not_in(scope.project_ids)
            ),
        )
        .limit(1)
    )
    hidden_message = await session.scalar(
        select(Message.id)
        .where(
            Message.account_id == conversation.account_id,
            Message.chat_id == conversation.chat_id,
            or_(Message.project_id.is_(None), Message.project_id.not_in(scope.project_ids)),
        )
        .limit(1)
    )
    # Scope completeness is independent of the bounded receipt page: a hidden
    # projected message can exist without a recent receipt or conversation row.
    scope_complete = other_project is None and hidden_message is None
    predicates = []
    for key in DIALOG_EVENT_KINDS:
        raw_event = TelegramReceipt.payload[key]
        predicates.append(
            and_(
                raw_event["business_connection_id"].as_string().in_(connections),
                raw_event["chat"]["id"].as_string() == conversation.chat_id,
            )
        )
    predicates_scope = or_(*predicates)
    continuation = []
    if before is not None:
        at, identifier = before
        continuation.append(
            or_(
                TelegramReceipt.received_at < at,
                and_(TelegramReceipt.received_at == at, TelegramReceipt.id < identifier),
            )
        )
    if receipt_id is not None:
        continuation.append(TelegramReceipt.id == receipt_id)
    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(
                TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
                predicates_scope,
                *continuation,
            )
            .order_by(TelegramReceipt.received_at.desc(), TelegramReceipt.id.desc())
            .limit(limit + 1)
        )
    ).all()
    result: list[dict[str, Any]] = []
    for receipt in reversed(receipts[:limit]):
        for key in DIALOG_EVENT_KINDS:
            event = receipt.payload.get(key)
            if isinstance(event, dict):
                sender = event.get("from")
                direction = (
                    "outbound"
                    if isinstance(sender, dict) and sender.get("id") == owner_id
                    else "inbound"
                    if isinstance(sender, dict)
                    else "unknown"
                )
                value = event.get("text", event.get("caption"))
                text_value = value if isinstance(value, str) and scope_complete else None
                result.append(
                    {
                        "event_ref": str(receipt.id),
                        "kind": key,
                        "direction": direction,
                        "received_at": receipt.received_at.isoformat(),
                        "text": text_value[text_offset : text_offset + 4096]
                        if text_value is not None
                        else None,
                        "text_offset": text_offset,
                        "text_truncated": text_value is not None
                        and len(text_value) > text_offset + 4096,
                        "projection_status": receipt.status,
                        "content_available": scope_complete,
                        "content_type": next(
                            (
                                kind
                                for kind in (
                                    "voice",
                                    "photo",
                                    "document",
                                    "video",
                                    "audio",
                                    "sticker",
                                    "text",
                                )
                                if kind in event
                            ),
                            "other",
                        ),
                    }
                )
                break
    return result, len(receipts) > limit, scope_complete


async def known_dialog_gap(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation: Conversation,
) -> bool:
    """Consume sticky observed ingress failures; heartbeat never restores trust."""
    sticky = await session.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.action == "reply_ingress_unavailable",
            or_(AuditLog.account_id.is_(None), AuditLog.account_id == conversation.account_id),
        )
        .limit(1)
    )
    if sticky is not None:
        return True
    action = await session.scalar(
        select(AuditLog.action)
        .where(
            AuditLog.account_id == conversation.account_id,
            AuditLog.action.in_(("reply_dialog_ingress_gap", "reply_dialog_ingress_restored")),
            AuditLog.metadata_["chat_id"].as_string() == conversation.chat_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    return action == "reply_dialog_ingress_gap"


async def dialog_read_issue(
    session: AsyncSession,
    *,
    scope: AccessScope,
    conversation: Conversation,
    events: list[dict[str, Any]],
    latest_message_id: str | None,
    own_receipts: set[str] | None = None,
) -> str | None:
    """Require recent accepted events when checking the latest readable message."""
    if await known_dialog_gap(session, scope=scope, conversation=conversation):
        return "known_ingress_gap"
    connections = select(TelegramBusinessConnection.business_connection_id).where(
        TelegramBusinessConnection.account_id == conversation.account_id
    )
    predicates = []
    for kind in DIALOG_EVENT_KINDS:
        predicates.append(_receipt_matches_dialog(
            TelegramReceipt.payload[kind], connections=connections, conversation=conversation,
        ))
    pending_query = select(TelegramReceipt.id).where(
        TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
        TelegramReceipt.status != TelegramReceiptStatus.PROCESSED.value,
        TelegramReceipt.id.not_in([UUID(value) for value in own_receipts or set()]),
        or_(*predicates),
    )
    visible = {UUID(event["event_ref"]) for event in events}
    visible_processed_messages = [
        UUID(event["event_ref"])
        for event in events
        if event["kind"] in ("business_message", "message")
        and event["projection_status"] == TelegramReceiptStatus.PROCESSED.value
    ]
    if latest_message_id is not None and latest_message_id.isdecimal():
        # Once the current message is visible and processed, historical
        # receipts cannot make that message unreadable.
        latest_readable_at = None
        if visible_processed_messages:
            latest_readable_at = await session.scalar(
                select(func.max(TelegramReceipt.received_at)).where(
                    TelegramReceipt.id.in_(visible_processed_messages),
                    or_(
                        TelegramReceipt.payload["business_message"]["message_id"].as_string()
                        == latest_message_id,
                        TelegramReceipt.payload["message"]["message_id"].as_string()
                        == latest_message_id,
                    ),
                )
            )
        if latest_readable_at is None:
            # Telegram can confirm a Gate send without echoing it as a webhook.
            latest_readable_at = await session.scalar(
                select(func.max(Message.sent_at)).where(
                    Message.account_id == conversation.account_id,
                    Message.project_id == conversation.project_id,
                    Message.chat_id == conversation.chat_id,
                    Message.business_connection_id.is_not_distinct_from(
                        conversation.business_connection_id
                    ),
                    Message.telegram_message_id == int(latest_message_id),
                    Message.direction == MessageDirection.OUTBOUND.value,
                    Message.source.in_((
                        "gate-approved-draft", "gate-approved-document", "gate-approved-media",
                    )),
                )
            )
        if latest_readable_at is not None:
            pending_query = pending_query.where(or_(
                TelegramReceipt.received_at >= latest_readable_at,
                TelegramReceipt.id.in_(visible),
            ))
    pending = (await session.scalars(pending_query.limit(21))).all()
    if any(identifier not in visible for identifier in pending):
        return "raw_readset_incomplete"
    pending_ids = {str(identifier) for identifier in pending}
    if any(event["event_ref"] in pending_ids and event["text_truncated"] for event in events):
        return "raw_text_truncated"
    return None
