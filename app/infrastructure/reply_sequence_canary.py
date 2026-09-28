"""Durable, irreversible attempt budget for an exact owner canary scope.

This primitive does not arm a scope or authorize a send by itself. Callers must
reserve before every provider HTTP attempt, including notification retries.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.models.reply_sequences import ReplyCanaryAttempt, ReplyCanaryScope
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class CanaryDenied(Exception):
    """Stable denial without account, dialog, body or provider payload."""


@dataclass(frozen=True, slots=True)
class CanaryAttemptRequest:
    workspace_ref: str
    execution_id: str
    attempt_key: UUID
    kind: str
    principal_subject: str
    account_id: UUID | None
    dialog_ref: str | None
    body_sha256: str | None
    notification_destination: str | None
    notification_template: str | None


async def reserve_canary_attempt(session: AsyncSession, request: CanaryAttemptRequest) -> int:
    """Lock the one workspace budget and consume one attempt before external I/O.

    The reservation is never refunded. A crash after commit may underuse the cap,
    but two processes cannot exceed it. Reusing an attempt key is denied.
    """

    scope = await session.scalar(
        select(ReplyCanaryScope)
        .where(ReplyCanaryScope.workspace_ref == request.workspace_ref)
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        scope is None
        or now is None
        or scope.state != "active"
        or scope.execution_id != request.execution_id
        or scope.principal_subject != request.principal_subject
        or scope.expires_at <= now
        or scope.attempts_reserved >= scope.max_attempts
    ):
        raise CanaryDenied("canary_admission_closed")
    if request.kind == "sequence":
        permitted = (
            request.account_id is not None
            and request.dialog_ref is not None
            and request.body_sha256 is not None
            and (
                f"{request.account_id}:{request.dialog_ref}:{request.body_sha256}"
                in scope.sequence_grants
            )
            and request.notification_destination is None
            and request.notification_template is None
        )
    elif request.kind == "notification":
        permitted = (
            request.notification_destination == scope.notification_destination
            and request.notification_template == scope.notification_template
            and request.account_id is None
            and request.dialog_ref is None
            and request.body_sha256 is None
        )
    else:
        permitted = False
    if not permitted:
        raise CanaryDenied("canary_scope_mismatch")
    reused = await session.scalar(
        select(ReplyCanaryAttempt.id).where(
            ReplyCanaryAttempt.workspace_ref == request.workspace_ref,
            ReplyCanaryAttempt.attempt_key == request.attempt_key,
        )
    )
    if reused is not None:
        raise CanaryDenied("canary_attempt_reused")
    scope.attempts_reserved += 1
    session.add(
        ReplyCanaryAttempt(
            id=uuid4(),
            workspace_ref=request.workspace_ref,
            attempt_key=request.attempt_key,
            kind=request.kind,
            execution_id=request.execution_id,
            reserved_at=now,
        )
    )
    await session.commit()
    return scope.attempts_reserved
