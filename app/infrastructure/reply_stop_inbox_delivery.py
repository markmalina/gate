"""Publish one owner notice through an explicitly supplied, private inbox client."""

from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.mcp_security import AccessScope
from app.infrastructure.reply_sequence_state import reply_stop_notice_outcome_id
from app.infrastructure.reply_sequence_stop_notice import read_reply_stop_notice
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_github import GithubReceipt, WatchVoiceError, body_digest
from app.models.domain import AuditLog

_State = Literal["route_unavailable", "unknown", "published", "rejected"]


class ReplyStopInboxClient(AppInboxClient):
    """Use the same private-repository checks with only the dedicated author."""

    def __init__(self, http: httpx.AsyncClient, token: str, *, app_slug: str) -> None:
        super().__init__(http, token, app_slug=app_slug)
        self.legacy_writer = None


@dataclass(frozen=True, slots=True)
class ReplyStopDelivery:
    state: _State
    issue_number: int | None = None


async def deliver_reply_stop_notice(
    sessions: async_sessionmaker[AsyncSession],
    *,
    event_id: UUID,
    scope: AccessScope,
    inbox: ReplyStopInboxClient | None,
) -> ReplyStopDelivery:
    if inbox is None or inbox.legacy_writer is not None:
        return ReplyStopDelivery("route_unavailable")

    claim_id = uuid5(NAMESPACE_URL, f"gate:reply-stop-notice:claim:{event_id}")
    outcome_id = reply_stop_notice_outcome_id(event_id)
    async with sessions() as session:
        notice = await read_reply_stop_notice(session, event_id=event_id, scope=scope)
    if notice is None:
        return ReplyStopDelivery("route_unavailable")
    body = (
        f"{notice.text}\n\n@markmalina\n\n"
        f"<!-- gate-reply-stop-notice:v1 {notice.idempotency_key} -->"
    )
    binding = {"event_id": str(event_id), "body_sha256": body_digest(body)}
    try:
        await inbox.preflight()
    except WatchVoiceError:
        return ReplyStopDelivery("route_unavailable")

    # Commit the one-use claim before any POST. A restarted claimant can only read back.
    async with sessions.begin() as session:
        claim = await session.scalar(
            insert(AuditLog).values(
                id=claim_id, actor=inbox.writer, action="reply_stop_notice_claimed",
                metadata_=binding,
            ).on_conflict_do_nothing(index_elements=[AuditLog.id]).returning(AuditLog.id)
        )
        if claim is None:
            existing = await session.get(AuditLog, claim_id)
            if existing is None or existing.actor != inbox.writer or existing.metadata_ != binding:
                return ReplyStopDelivery("unknown")
            outcome = await session.get(AuditLog, outcome_id)
            if outcome is not None:
                number = outcome.metadata_.get("issue_number")
                if outcome.actor != inbox.writer or any(
                    outcome.metadata_.get(key) != value for key, value in binding.items()
                ):
                    return ReplyStopDelivery("unknown")
                if outcome.action == "reply_stop_notice_published" and type(number) is int:
                    return ReplyStopDelivery("published", number)
                if outcome.action == "reply_stop_notice_rejected" and number is None:
                    return ReplyStopDelivery("rejected")
                return ReplyStopDelivery("unknown")

    receipt: GithubReceipt | None
    try:
        if claim is not None:
            receipt = await inbox.post(
                part=0, issue_number=None, title="Серия ответов остановлена", body=body,
            )
        else:
            receipt = await inbox.reconcile(part=0, issue_number=None, body=body)
    except WatchVoiceError as error:
        if claim is None or error.unknown:
            return ReplyStopDelivery("unknown")
        state: Literal["published", "rejected"] = "rejected"
        number = None
    else:
        if receipt is None:
            return ReplyStopDelivery("unknown")
        state = "published"
        number = receipt.number

    async with sessions.begin() as session:
        await session.execute(
            insert(AuditLog).values(
                id=outcome_id, actor=inbox.writer, action=f"reply_stop_notice_{state}",
                metadata_={**binding, "issue_number": number},
            ).on_conflict_do_nothing(index_elements=[AuditLog.id])
        )
    return ReplyStopDelivery(state, number)
