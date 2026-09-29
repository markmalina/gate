"""Provider-free canary controls for the authenticated, pinned Ops executor.

Inputs contain protected scope; receipts contain only counts and digests. The
caller owns authentication, fleet/config/DB verification and operation journal.
These functions never turn an unverified string into infrastructure evidence.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.reply_sequence_canary import CanaryDenied
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_owner_route import owner_route
from app.models.domain import Conversation, ReplyDraft
from app.models.reply_sequences import (
    ReplyCanaryAttempt,
    ReplyCanaryScope,
    ReplyConnectorAdmission,
    ReplyContinuityEpoch,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplySequence,
    ReplySequenceNotification,
)

if TYPE_CHECKING:
    from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime


@dataclass(frozen=True)
class CanaryGrant:
    account_id: UUID
    recipient: str
    draft_id: UUID
    body_sha256: str


@dataclass(frozen=True)
class CanaryArm:
    execution_id: str
    principal_subject: str
    expected_generation: int
    bot_resource_id: str
    grants: tuple[CanaryGrant, ...]
    notification_destination: str
    expires_at: datetime
    max_attempts: int

    def digest(self, workspace: str) -> str:
        payload = {
            "workspace": workspace,
            "execution": self.execution_id,
            "principal": self.principal_subject,
            "generation": self.expected_generation,
            "bot": self.bot_resource_id,
            "notification": self.notification_destination,
            "expires": self.expires_at.isoformat(),
            "cap": self.max_attempts,
            "grants": sorted(
                (str(g.account_id), g.recipient, str(g.draft_id), g.body_sha256)
                for g in self.grants
            ),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def receipt(scope: ReplyCanaryScope, now: datetime) -> dict[str, object]:
    return {
        "schema_version": "gate.reply-canary.v1",
        "scope_sha256": scope.scope_sha256,
        "state": scope.state,
        "expires_at": scope.expires_at.isoformat(),
        "max_attempts": scope.max_attempts,
        "attempts_reserved": scope.attempts_reserved,
        "admission_open": scope.state == "active"
        and scope.expires_at > now
        and scope.attempts_reserved < scope.max_attempts,
    }


async def arm_canary(runtime: "ReplySequenceRuntime", request: CanaryArm) -> dict[str, object]:
    if (
        not runtime.settings.gate_reply_sequence_canary_enabled
        or request.bot_resource_id != runtime.bot_resource
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request.execution_id)
        or not request.principal_subject
        or request.expires_at.tzinfo is None
        or type(request.max_attempts) is not int
        or not 1 <= request.max_attempts <= 6
        or not 2 <= len(request.grants) <= 100
        or len({g.draft_id for g in request.grants}) != len(request.grants)
        or any(not re.fullmatch(r"[0-9a-f]{64}", g.body_sha256) for g in request.grants)
    ):
        raise CanaryDenied("canary_invalid_scope")
    digest = request.digest(runtime.workspace)
    async with runtime.sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace, with_for_update=True)
        now = await runtime.db_now(session)
        existing = await session.get(ReplyCanaryScope, runtime.workspace, with_for_update=True)
        if existing is not None:
            if existing.execution_id != request.execution_id or existing.scope_sha256 != digest:
                raise CanaryDenied("canary_scope_immutable")
            return receipt(existing, now)  # reconciliation, never reset/extend/re-arm
        connector = await session.scalar(
            select(ReplyConnectorAdmission).where(
                ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                ReplyConnectorAdmission.connector_key == "telegram_business",
            )
        )
        epoch = (
            await session.get(ReplyContinuityEpoch, connector.epoch_id)
            if connector is not None and connector.epoch_id
            else None
        )
        if epoch is None or epoch.bot_resource_id != runtime.bot_resource:
            raise CanaryDenied("canary_verified_bot_identity_required")
        if (
            control is None
            or not control.migration_complete
            or control.mode != "disabled"
            or control.generation != request.expected_generation
            or not 0 < (request.expires_at - now).total_seconds() <= 600
            or await session.scalar(
                select(ReplySequence.id)
                .where(
                    ReplySequence.workspace_ref == runtime.workspace,
                    ReplySequence.state == "active",
                )
                .limit(1)
            )
            is not None
            or await session.scalar(
                select(ReplyDispatchPermit.slot)
                .where(
                    ReplyDispatchPermit.workspace_ref == runtime.workspace,
                    ReplyDispatchPermit.state != "free",
                )
                .limit(1)
            )
            is not None
            or await session.scalar(
                select(ReplySequenceNotification.id)
                .join(ReplySequence)
                .where(
                    ReplySequence.workspace_ref == runtime.workspace,
                    ReplySequenceNotification.delivery_state == "sending",
                )
                .limit(1)
            )
            is not None
        ):
            raise CanaryDenied("canary_not_drained")
        grants = []
        destination_verified = False
        for grant in request.grants:
            row = (
                await session.execute(
                    select(ReplyDraft, Conversation.chat_id)
                    .join(Conversation, ReplyDraft.conversation_id == Conversation.id)
                    .where(ReplyDraft.id == grant.draft_id)
                )
            ).first()
            if (
                row is None
                or row[0].account_id != grant.account_id
                or row[1] != grant.recipient
                or hashlib.sha256(row[0].draft_text.encode()).hexdigest() != grant.body_sha256
            ):
                raise CanaryDenied("canary_draft_mismatch")
            route = await owner_route(
                session,
                account_id=grant.account_id,
                principal_subject=request.principal_subject,
                recipient=grant.recipient,
            )
            if route is None:
                raise CanaryDenied("canary_owner_route_unverified")
            destination_verified |= str(route.user_chat_id) == request.notification_destination
            grants.extend(
                [
                    f"{grant.account_id}:{grant.recipient}:{grant.body_sha256}",
                    f"draft:{grant.draft_id}:{grant.body_sha256}",
                ]
            )
        if not destination_verified:
            raise CanaryDenied("canary_owner_route_unverified")
        scope = ReplyCanaryScope(
            workspace_ref=runtime.workspace,
            execution_id=request.execution_id,
            state="active",
            principal_subject=request.principal_subject,
            sequence_grants=sorted(set(grants)),
            notification_destination=request.notification_destination,
            notification_template="reply_sequence_stop_v1",
            expires_at=request.expires_at,
            max_attempts=request.max_attempts,
            attempts_reserved=0,
            scope_sha256=digest,
        )
        session.add(scope)
        await session.commit()
        return receipt(scope, now)


async def admit_canary_queue(
    session: AsyncSession,
    *,
    runtime: "ReplySequenceRuntime",
    principal: str,
    account_id: UUID,
    recipient: str,
    drafts: list[tuple[UUID, str]],
    now: datetime,
) -> str | None:
    scope = await session.get(ReplyCanaryScope, runtime.workspace, with_for_update=True)
    enabled = runtime.settings.gate_reply_sequence_canary_enabled
    if not enabled and (scope is None or scope.state == "closed"):
        return None
    if (
        not enabled
        or scope is None
        or scope.state != "active"
        or scope.expires_at <= now
        or scope.attempts_reserved >= scope.max_attempts
        or scope.principal_subject != principal
        or any(
            f"{account_id}:{recipient}:{body}" not in scope.sequence_grants
            or f"draft:{draft}:{body}" not in scope.sequence_grants
            for draft, body in drafts
        )
    ):
        raise CanaryDenied("canary_admission_closed")
    return scope.execution_id


async def canary_status(runtime: "ReplySequenceRuntime") -> dict[str, object]:
    async with runtime.sessions() as session:
        scope = await session.get(ReplyCanaryScope, runtime.workspace)
        if scope is None:
            return {"schema_version": "gate.reply-canary.v1", "state": "unarmed"}
        result = receipt(scope, await runtime.db_now(session))
        result["ledger_attempts"] = await session.scalar(
            select(func.count())
            .select_from(ReplyCanaryAttempt)
            .where(ReplyCanaryAttempt.workspace_ref == runtime.workspace)
        )
        return result


async def canary_sequence_allowed(
    session: AsyncSession,
    runtime: "ReplySequenceRuntime",
    sequence: ReplySequence,
    now: datetime,
) -> bool:
    scope = await session.get(ReplyCanaryScope, runtime.workspace)
    enabled = runtime.settings.gate_reply_sequence_canary_enabled
    if sequence.canary_execution_id is None:
        return not enabled and (scope is None or scope.state == "closed")
    return bool(
        enabled
        and scope
        and scope.state == "active"
        and sequence.canary_execution_id == scope.execution_id
        and scope.expires_at > now
        and scope.attempts_reserved < scope.max_attempts
    )


async def reconcile_canary_once(runtime: "ReplySequenceRuntime") -> None:
    async with runtime.sessions() as session:
        scope = await session.get(ReplyCanaryScope, runtime.workspace)
        now = await runtime.db_now(session)
        if scope is None:
            return
        expired = scope.expires_at <= now or scope.attempts_reserved >= scope.max_attempts
        execution_id = scope.execution_id
        close = scope.state == "active" and (
            expired or not runtime.settings.gate_reply_sequence_canary_enabled
        )
        if scope.state == "closed":
            remaining = await session.scalar(
                select(ReplySequence.id)
                .where(
                    ReplySequence.workspace_ref == runtime.workspace,
                    ReplySequence.canary_execution_id == execution_id,
                    ReplySequence.state == "active",
                )
                .limit(1)
            )
            pending = await session.scalar(
                select(ReplySequenceNotification.id)
                .join(ReplySequence)
                .where(
                    ReplySequence.workspace_ref == runtime.workspace,
                    ReplySequence.canary_execution_id == execution_id,
                    ReplySequenceNotification.delivery_state == "pending",
                )
                .limit(1)
            )
            close = remaining is not None or pending is not None
    if close:
        await close_canary(runtime, execution_id=execution_id)


async def close_canary(runtime: "ReplySequenceRuntime", *, execution_id: str) -> dict[str, object]:
    # Commit admission fence before taking per-dialog locks. Retry completes the
    # remaining stops after a crash; it never repeats an HTTP effect.
    async with runtime.sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace, with_for_update=True)
        scope = await session.get(ReplyCanaryScope, runtime.workspace, with_for_update=True)
        if scope is None or scope.execution_id != execution_id or control is None:
            raise CanaryDenied("canary_scope_mismatch")
        scope.state = "closed"
        if control.mode != "disabled":
            control.mode = "disabled"
            control.generation += 1
        await session.commit()
        rows = (
            await session.execute(
                select(
                    ReplySequence.id, ReplySequence.account_id, ReplyDialogGuard.provider_chat_id
                )
                .join(ReplyDialogGuard)
                .where(
                    ReplySequence.workspace_ref == runtime.workspace,
                    ReplySequence.canary_execution_id == execution_id,
                    ReplySequence.state == "active",
                )
            )
        ).all()
    for identity, account, chat in rows:
        async with dialog_guard(
            runtime.ingress_guards, workspace=runtime.workspace, account_id=account, chat_id=chat
        ):
            async with runtime.sessions() as session:
                sequence = await session.get(ReplySequence, identity, with_for_update=True)
                if sequence is not None and sequence.state == "active":
                    await runtime.stop(
                        session,
                        sequence,
                        reason="canary_closed",
                        now=await runtime.db_now(session),
                        notify=False,
                    )
                await session.commit()
    async with runtime.sessions() as session:
        await session.execute(
            update(ReplySequenceNotification)
            .where(
                ReplySequenceNotification.sequence_id.in_(
                    select(ReplySequence.id).where(
                        ReplySequence.workspace_ref == runtime.workspace,
                        ReplySequence.canary_execution_id == execution_id,
                    )
                ),
                ReplySequenceNotification.delivery_state == "pending",
            )
            .values(
                delivery_state="failed",
                safe_error_code="canary_closed",
                finished_at=await runtime.db_now(session),
            )
        )
        await session.commit()
    return await canary_status(runtime)
