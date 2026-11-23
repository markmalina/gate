"""Project a committed and scoped series stop into a content-free notice."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mcp_security import AccessScope
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.reply_sequence_state import KEY
from app.models.domain import Account, AuditLog, ReplyDraft, TelegramReceipt


@dataclass(frozen=True, slots=True)
class ReplyStopNotice:
    """A stable source key and owner-visible text for one stopped series."""

    idempotency_key: UUID
    text: str = "Gate остановил серию после нового события в диалоге."


async def read_reply_stop_notice(
    session: AsyncSession, *, event_id: UUID, scope: AccessScope,
) -> ReplyStopNotice | None:
    if not scope.is_explicit:
        return None
    audit_sequence = AuditLog.metadata_["sequence_id"].as_string()
    audit_receipt = AuditLog.metadata_["event_ref"].as_string()
    draft_sequence = ReplyDraft.prompt_context[KEY]
    stop_exists = select(ReplyDraft.id).where(
        ReplyDraft.account_id == AuditLog.account_id,
        ReplyDraft.project_id == AuditLog.project_id,
        draft_sequence["id"].as_string() == audit_sequence,
        draft_sequence["workspace_id"].as_string() == scope.workspace_id,
        draft_sequence["state"].as_string() == "stopped",
        draft_sequence["stop_reason"].as_string() == "context_changed",
        draft_sequence["stop_event_ref"].as_string() == audit_receipt,
    ).exists()
    receipt_ref = await session.scalar(
        select(audit_receipt)
        .select_from(AuditLog)
        .join(Account, Account.id == AuditLog.account_id)
        .where(
            AuditLog.id == event_id,
            AuditLog.actor == "gate",
            AuditLog.action == "reply_sequence_stopped",
            AuditLog.account_id.in_(scope.account_ids),
            AuditLog.project_id.in_(scope.project_ids),
            Account.owner == scope.workspace_id,
            Account.is_active.is_(True),
            Account.read_enabled.is_(True),
            AuditLog.metadata_["reason"].as_string() == "context_changed",
            stop_exists,
        )
    )
    if receipt_ref is None:
        return None
    try:
        receipt_id = UUID(receipt_ref)
    except ValueError:
        return None
    accepted = await session.scalar(select(TelegramReceipt.id).where(
        TelegramReceipt.id == receipt_id,
        TelegramReceipt.bot_key == TelegramConnectorKey.BUSINESS.value,
    ))
    return ReplyStopNotice(event_id) if accepted is not None else None
