"""Sequence state in the existing draft journal; callers own the ingress fence.

No scheduler, inferred text identity or fresh-snapshot rebinding lives here.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.reply_sends import ReplySendError
from app.core.mcp_security import AccessScope
from app.models.domain import Account, AuditLog, Message, ReplyDraft, SendAttempt

KEY = "reply_sequence"


async def stop_sequences(
    session: AsyncSession, *, account_id: UUID | None = None, chat_id: str | None = None,
    except_sequence: str | None = None, reason: str = "context_changed",
    event_ref: str | None = None,
) -> None:
    """Stop every matching series, without a discovery/page limit or attempt mutation."""
    predicates = [ReplyDraft.prompt_context.has_key(KEY)]  # noqa: E711
    if account_id is not None:
        predicates.append(ReplyDraft.account_id == account_id)
    if chat_id is not None:
        predicates.append(
            ReplyDraft.prompt_context[KEY]["expected_recipient_chat_id"].as_string() == chat_id
        )
    drafts = (await session.scalars(
        select(ReplyDraft).where(*predicates).order_by(ReplyDraft.id).with_for_update()
    )).all()
    stopped: set[str] = set()
    for draft in drafts:
        context = dict(draft.prompt_context or {})
        metadata = context.get(KEY)
        if not isinstance(metadata, dict) or metadata.get("id") == except_sequence:
            continue
        # The terminal stop latch is independent of an item's delivery outcome.
        # A SENDING/UNKNOWN attempt may still complete; do not cancel that attempt.
        if metadata.get("state") != "scheduled" or metadata.get("stop_reason"):
            continue
        metadata = dict(metadata)
        metadata.update(
            state="stopped", error_code="context_changed", stop_reason=reason,
            stopped_at=datetime.now(UTC).isoformat(), stop_event_ref=event_ref,
            auto_resume=False,
        )
        context[KEY] = metadata
        draft.prompt_context = context
        identifier = str(metadata.get("id"))
        if identifier not in stopped:
            session.add(AuditLog(
                actor="gate", action="reply_sequence_stopped",
                account_id=draft.account_id, project_id=draft.project_id,
                metadata_={"sequence_id": identifier, "reason": reason,
                           "event_ref": event_ref, "auto_resume": False,
                           "notification_status": "route_unavailable"},
            ))
            stopped.add(identifier)
    await session.flush()


async def confirmed_echo_sequence(
    session: AsyncSession, *, account_id: UUID, connection_id: str, chat_id: str,
    payload: dict[str, Any], receipt_id: UUID,
) -> str | None:
    """Only an authenticated original outbound envelope + confirmed exact receipt."""
    event = payload.get("business_message", payload.get("message"))
    if not isinstance(event, dict):
        return None
    sender = event.get("from")
    owner = await session.scalar(select(Account.telegram_user_id).where(Account.id == account_id))
    if not isinstance(sender, dict) or owner is None or sender.get("id") != owner:
        return None
    message_id = event.get("message_id")
    if type(message_id) is not int or message_id <= 0:
        return None
    rows = (await session.execute(
        select(SendAttempt, ReplyDraft)
        .join(ReplyDraft, SendAttempt.reply_draft_id == ReplyDraft.id)
        .where(
            SendAttempt.account_id == account_id,
            SendAttempt.business_connection_id == connection_id,
            SendAttempt.target_chat_id == chat_id,
            SendAttempt.provider_message_id == str(message_id),
            SendAttempt.status == "sent", SendAttempt.finished_at.is_not(None),
            ReplyDraft.account_id == account_id,
            ReplyDraft.project_id == SendAttempt.project_id,
            ReplyDraft.conversation_id == SendAttempt.conversation_id,
            ReplyDraft.reply_task_id == SendAttempt.reply_task_id,
            ReplyDraft.prompt_context[KEY]["expected_recipient_chat_id"].as_string() == chat_id,
            ReplyDraft.prompt_context[KEY]["principal_subject"].as_string()
            == SendAttempt.principal_subject,
            ReplyDraft.prompt_context.has_key(KEY),  # noqa: E711
        )
    )).all()
    if len(rows) != 1:
        return None
    attempt, draft = rows[0]
    metadata = (draft.prompt_context or {}).get(KEY)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        return None
    session.add(AuditLog(
        actor="gate", action="reply_sequence_own_echo",
        account_id=account_id, project_id=draft.project_id,
        metadata_={"sequence_id": metadata["id"], "draft_id": str(draft.id),
                   "attempt_id": str(attempt.id), "receipt_id": str(receipt_id),
                   "update_id": payload.get("update_id"), "message_id": message_id,
                   "connection_id": connection_id, "chat_id": chat_id},
    ))
    await session.flush()
    return str(metadata["id"])


async def sequence_progress(
    session: AsyncSession, *, draft: ReplyDraft, current_attempt_id: UUID | None = None,
) -> tuple[dict[str, int], set[UUID], set[str]]:
    """Subtract only journal-proven own effects from the immutable baseline.

    Unconfirmed early echoes stop the series permanently. Later success can
    explain them, but cannot remove the stop latch or retry UNKNOWN.
    """
    metadata = (draft.prompt_context or {}).get(KEY)
    if not isinstance(metadata, dict) or metadata.get("state") != "scheduled":
        raise ReplySendError(code="context_changed", message="Sequence is stopped")
    sequence_id = metadata.get("id")
    position = metadata.get("position")
    if type(position) is not int or not 0 <= position < 50:
        raise ReplySendError(code="context_changed", message="Invalid sequence position")
    rows = (await session.execute(
        select(ReplyDraft, SendAttempt)
        .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
        .where(
            ReplyDraft.account_id == draft.account_id,
            ReplyDraft.project_id == draft.project_id,
            ReplyDraft.conversation_id == draft.conversation_id,
            ReplyDraft.prompt_context[KEY]["id"].as_string() == sequence_id,
        )
    )).all()
    by_position = {(row.prompt_context or {})[KEY].get("position"): (row, attempt)
                   for row, attempt in rows}
    size = metadata.get("size")
    if (type(size) is not int or not 2 <= size <= 50 or len(rows) != size
            or set(by_position) != set(range(size))):
        raise ReplySendError(code="context_changed", message="Incomplete sequence journal")
    revisions: dict[str, int] = {}
    ignored_messages: set[UUID] = set()
    ignored_receipts: set[str] = set()
    for index in range(size):
        item, attempt = by_position[index]
        item_meta = (item.prompt_context or {})[KEY]
        if (item.reply_task_id != draft.reply_task_id
                or (item.prompt_context or {}).get("dialog_snapshot")
                != (draft.prompt_context or {}).get("dialog_snapshot")
                or any(item_meta.get(key) != metadata.get(key) for key in (
                    "size", "interval_seconds", "workspace_id", "principal_subject",
                    "expected_account", "expected_recipient_chat_id",
                )) or item_meta.get("interval_seconds") != 60):
            raise ReplySendError(code="context_changed", message="Changed sequence binding")
        if attempt is not None and (
            attempt.account_id != draft.account_id or attempt.project_id != draft.project_id
            or attempt.conversation_id != draft.conversation_id
            or attempt.target_chat_id != metadata.get("expected_recipient_chat_id")
            or attempt.principal_subject != metadata.get("principal_subject")
        ):
            raise ReplySendError(code="context_changed", message="Changed attempt binding")
        if item_meta.get("stop_reason") or item_meta.get("state") in ("stopped", "cancelled"):
            raise ReplySendError(code="context_changed", message="Sequence is stopped")
        if index < position:
            if (attempt is None or attempt.status != "sent" or attempt.finished_at is None
                    or not attempt.provider_message_id):
                raise ReplySendError(code="context_changed", message="Unconfirmed predecessor")
            if datetime.now(UTC) < attempt.finished_at + timedelta(seconds=60):
                raise ReplySendError(code="sequence_not_due", message="Wait after confirmed send")
        elif attempt is not None and attempt.id != current_attempt_id:
            raise ReplySendError(code="context_changed", message="Unexpected sequence attempt")
        if attempt is None:
            continue
        # Reservation audit proves exactly one increment; legacy/unbound journal refuses.
        reservation = await session.scalar(select(AuditLog.id).where(
            AuditLog.action == "reply_send_reserved_from_mcp",
            AuditLog.account_id == draft.account_id, AuditLog.project_id == draft.project_id,
            AuditLog.metadata_["attempt_id"].as_string() == str(attempt.id),
            AuditLog.metadata_["draft_id"].as_string() == str(item.id),
        ))
        if reservation is None or attempt.business_connection_id is None:
            raise ReplySendError(code="context_changed", message="Missing reservation proof")
        connection = attempt.business_connection_id
        revisions[connection] = revisions.get(connection, 0) + 1
        if attempt.status != "sent":
            if attempt.id != current_attempt_id or attempt.status != "sending":
                raise ReplySendError(code="context_changed", message="Ambiguous own progress")
            continue
        echoes = (await session.scalars(select(AuditLog.metadata_).where(
            AuditLog.action == "reply_sequence_own_echo",
            AuditLog.account_id == draft.account_id, AuditLog.project_id == draft.project_id,
            AuditLog.metadata_["sequence_id"].as_string() == sequence_id,
            AuditLog.metadata_["attempt_id"].as_string() == str(attempt.id),
        ))).all()
        update_ids = [echo["update_id"] for echo in echoes]
        ignored_receipts.update(echo["receipt_id"] for echo in echoes)
        revisions[connection] += len(echoes)
        provider_id = attempt.provider_message_id
        messages = (await session.scalars(select(Message).where(
            Message.account_id == draft.account_id, Message.project_id == draft.project_id,
            Message.chat_id == attempt.target_chat_id,
            Message.business_connection_id == connection, Message.direction == "outbound",
            Message.telegram_message_id == int(provider_id),
        ))).all() if provider_id and provider_id.isdigit() else []
        for message in messages:
            proven = (
                (message.source == "gate-approved-draft"
                 and (message.payload or {}).get("_gate_send_attempt_id") == str(attempt.id))
                or (message.source == "telegram" and message.telegram_update_id in update_ids)
            )
            if proven and message.text == item.draft_text:
                ignored_messages.add(message.id)
    return revisions, ignored_messages, ignored_receipts


async def scoped_sequence_status(
    session: AsyncSession, *, scope: AccessScope, task_id: UUID,
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(ReplyDraft, SendAttempt)
        .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
        .where(
            ReplyDraft.reply_task_id == task_id,
            ReplyDraft.account_id.in_(scope.account_ids),
            ReplyDraft.project_id.in_(scope.project_ids),
            ReplyDraft.prompt_context[KEY]["workspace_id"].as_string() == scope.workspace_id,
        ).order_by(ReplyDraft.created_at, ReplyDraft.id)
    )).all()
    groups: dict[str, dict[str, Any]] = {}
    for draft, attempt in rows:
        metadata = (draft.prompt_context or {})[KEY]
        identifier = metadata["id"]
        group = groups.setdefault(identifier, {
            "sequence_id": identifier, "state": "scheduled", "stop_reason": None,
            "auto_resume": False, "notification_status": "route_unavailable", "items": [],
        })
        if metadata.get("stop_reason") or metadata.get("state") in ("stopped", "cancelled"):
            group.update(state="stopped", stop_reason=metadata.get("stop_reason")
                         or metadata.get("error_code"))
        if attempt is not None and attempt.status in ("unknown", "failed"):
            group.update(state="stopped", stop_reason=group["stop_reason"]
                         or attempt.error_code or "ambiguous_attempt")
        group["items"].append({
            "draft_id": str(draft.id), "position": metadata["position"],
            "state": attempt.status if attempt else metadata["state"],
            "attempt_id": str(attempt.id) if attempt else None,
            "provider_message_id": attempt.provider_message_id if attempt else None,
            "error_code": attempt.error_code if attempt else metadata.get("error_code"),
        })
    for group in groups.values():
        group["items"].sort(key=lambda item: item["position"])
        group["sent_count"] = sum(item["state"] == "sent" for item in group["items"])
        group["remaining_count"] = len(group["items"]) - group["sent_count"]
        group["ambiguous_count"] = sum(item["state"] in ("reserved", "sending", "unknown")
                                       for item in group["items"])
        if group["sent_count"] == len(group["items"]):
            group["state"] = "sent"
    return list(groups.values())
