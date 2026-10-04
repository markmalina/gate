"""Read-only metadata for one granted RU/BY owner-to-owner Business dialog.

No message text, raw payload, contact data, credentials, provider call or mutation
belongs to this capability. An unbound observation is time-stamped evidence, not
a guarantee against a binding being changed after the query.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.account_labels import account_label
from app.application.worker_health import REPLY_SEQUENCE_WORKER, WorkerHealthRegistry
from app.core.mcp_security import Principal
from app.core.telegram_connectors import TelegramConnectorKey
from app.models.agent import AgentTurnModel
from app.models.alpha import AgentInstallationModel
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import (
    DEFAULT_PROJECT_ID,
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TELEGRAM_MARK_RU_ACCOUNT_ID,
    Account,
    Conversation,
    Message,
    ReplyDraft,
    ReplyTask,
    SendAttempt,
    TelegramBusinessConnection,
    TelegramReceipt,
)

OWNER_REPLY_READINESS_TOOL_NAME = "gate_get_owner_reply_readiness"
OWNER_REPLY_READINESS_SCOPE = "gate:inbox:read"
_OWNER_WORKSPACE = "mark-personal"
_ACCOUNT_IDS = {"RU": TELEGRAM_MARK_RU_ACCOUNT_ID, "BY": TELEGRAM_MARK_BY_ACCOUNT_ID}
_PROVIDER_ID_MAX = (1 << 52) - 1


class OwnerReplyReadinessError(ValueError):
    """Safe bounded error; never include database or provider exception details."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = "Owner reply metadata is unavailable for the exact permitted binding"
        super().__init__(self.message)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_PRIVATE_ID_SCHEMA = {"type": "string", "pattern": "^[1-9][0-9]{0,15}$"}
OWNER_REPLY_READINESS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "account_alias": {"type": "string", "enum": ["RU", "BY"]},
        "recipient_chat_id": _PRIVATE_ID_SCHEMA,
        "provider_message_id": _PRIVATE_ID_SCHEMA,
    },
    "required": ["account_alias", "recipient_chat_id"],
    "additionalProperties": False,
}
_NULLABLE_BOOLEAN = {"type": ["boolean", "null"]}
_COUNT = {"type": "integer", "minimum": 0}
_WORKER_SCHEMA = _object(
    {
        "available": {"type": "boolean"},
        "enabled": _NULLABLE_BOOLEAN,
        "state": {
            "type": ["string", "null"],
            "enum": ["disabled", "starting", "healthy", "degraded", None],
        },
        "consecutive_failures": {"type": ["integer", "null"], "minimum": 0},
        "heartbeat_age_seconds": {"type": ["number", "null"], "minimum": 0},
        "last_error_code": {"type": ["string", "null"]},
    }
)
_TASK_SCHEMA = _object(
    {
        "task_id": {"type": "string", "format": "uuid"},
        "status": {"type": "string", "enum": ["needs_reply", "replied", "dismissed", "snoozed"]},
        "follow_up_required": {"type": "boolean"},
        "has_inbound_within_24h": {"type": "boolean"},
        "exact_provider_message_match": {"type": "boolean"},
        "receipt_processed": _NULLABLE_BOOLEAN,
        "correlated_agent_turn_count": {"type": ["integer", "null"], "minimum": 0},
        "mayak_reason_code_persisted": {"type": "boolean", "const": False},
    }
)
OWNER_REPLY_READINESS_OUTPUT_SCHEMA = _object(
    {
        "status": {"type": "string", "enum": ["task_found", "no_task"]},
        "account_alias": {"type": "string", "enum": ["RU", "BY"]},
        "recipient_chat_id": _PRIVATE_ID_SCHEMA,
        "observed_at": {"type": "string", "format": "date-time"},
        "connection_enabled": {"type": "boolean"},
        "stored_can_reply": {"type": "boolean"},
        "task": {"anyOf": [_TASK_SCHEMA, {"type": "null"}]},
        "seed_observation": _object(
            {
                "state": {"type": "string", "enum": ["unbound", "blocked"]},
                "active_binding_count": _COUNT,
                "eligible_observed": {"type": "boolean"},
                "valid_for_seconds": {"type": "integer", "const": 60},
                "guarantees_zero_provider_calls": {"type": "boolean", "const": False},
            }
        ),
        "reply_sequence": _object(
            {
                "worker": _WORKER_SCHEMA,
                "pending_items": _COUNT,
                "pending_sequences": _COUNT,
                "unresolved_attempts": _COUNT,
                "malformed_items": _COUNT,
            }
        ),
    }
)


def _provider_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[1-9][0-9]{0,15}", value) is None
        or int(value) > _PROVIDER_ID_MAX
    ):
        raise OwnerReplyReadinessError("invalid_arguments")
    return value


def reply_sequence_worker_metadata(
    registry: WorkerHealthRegistry | None, *, now: datetime
) -> dict[str, Any]:
    """Reuse the same pure registry snapshot as /admin/ops/health, without HTTP."""
    if registry is not None:
        for worker in registry.snapshot(observed_at=now).workers:
            if worker.name == REPLY_SEQUENCE_WORKER:
                return {
                    "available": True,
                    "enabled": worker.enabled,
                    "state": worker.state.value,
                    "consecutive_failures": worker.consecutive_failures,
                    "heartbeat_age_seconds": worker.heartbeat_age_seconds,
                    "last_error_code": worker.last_error_code,
                }
    return {
        "available": False,
        "enabled": None,
        "state": None,
        "consecutive_failures": None,
        "heartbeat_age_seconds": None,
        "last_error_code": None,
    }


async def build_owner_reply_readiness(
    *,
    session: AsyncSession,
    principal: Principal,
    account_alias: str,
    recipient_chat_id: str,
    provider_message_id: str | None = None,
    worker_registry: WorkerHealthRegistry | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve scalar metadata only; never create tasks or invoke runtime services."""
    with session.no_autoflush:
        return await _owner_reply_readiness(
            session=session,
            principal=principal,
            account_alias=account_alias,
            recipient_chat_id=recipient_chat_id,
            provider_message_id=provider_message_id,
            worker_registry=worker_registry,
            now=now,
        )


async def _owner_reply_readiness(
    *,
    session: AsyncSession,
    principal: Principal,
    account_alias: str,
    recipient_chat_id: str,
    provider_message_id: str | None,
    worker_registry: WorkerHealthRegistry | None,
    now: datetime | None,
) -> dict[str, Any]:
    scope = principal.access_scope
    if (
        scope.workspace_id != _OWNER_WORKSPACE
        or not scope.is_explicit
        or OWNER_REPLY_READINESS_SCOPE not in principal.scopes
        or not set(_ACCOUNT_IDS.values()).issubset(scope.account_ids)
    ):
        raise OwnerReplyReadinessError("scope_denied")
    if not isinstance(account_alias, str) or account_alias not in _ACCOUNT_IDS:
        raise OwnerReplyReadinessError("invalid_arguments")
    recipient_chat_id = _provider_id(recipient_chat_id)
    if provider_message_id is not None:
        provider_message_id = _provider_id(provider_message_id)
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise OwnerReplyReadinessError("invalid_arguments")
    observed = observed.astimezone(UTC)
    account_id = _ACCOUNT_IDS[account_alias]
    other_id = _ACCOUNT_IDS["BY" if account_alias == "RU" else "RU"]

    account_rows = (
        await session.execute(
            select(
                Account.id,
                Account.alias,
                Account.country_code,
                Account.telegram_user_id,
                Account.is_active,
                Account.read_enabled,
                Account.send_enabled,
            )
            .where(Account.id.in_(_ACCOUNT_IDS.values()))
            .limit(3)
        )
    ).all()
    by_id = {row.id: row for row in account_rows}
    if len(account_rows) != 2 or len(by_id) != 2:
        raise OwnerReplyReadinessError("owner_binding_unavailable")
    for alias, expected_id in _ACCOUNT_IDS.items():
        account = by_id.get(expected_id)
        if (
            account is None
            or account_label(alias=account.alias, country_code=account.country_code) != alias
            or not account.is_active
            or not account.read_enabled
        ):
            raise OwnerReplyReadinessError("owner_binding_unavailable")

    connections = (
        await session.execute(
            select(
                TelegramBusinessConnection.account_id,
                TelegramBusinessConnection.business_connection_id,
                TelegramBusinessConnection.telegram_user_id,
                TelegramBusinessConnection.user_chat_id,
                TelegramBusinessConnection.rights["can_reply"].label("can_reply"),
            )
            .where(
                TelegramBusinessConnection.account_id.in_(_ACCOUNT_IDS.values()),
                TelegramBusinessConnection.is_enabled.is_(True),
            )
            .limit(3)
        )
    ).all()
    connection_by_account = {row.account_id: row for row in connections}
    if len(connections) != 2 or len(connection_by_account) != 2:
        raise OwnerReplyReadinessError("owner_binding_unavailable")
    identities: dict[UUID, str] = {}
    for expected_id in _ACCOUNT_IDS.values():
        account = by_id[expected_id]
        connection = connection_by_account.get(expected_id)
        if connection is None:
            raise OwnerReplyReadinessError("owner_binding_unavailable")
        identity_values = {
            str(value)
            for value in (
                account.telegram_user_id,
                connection.telegram_user_id,
                connection.user_chat_id,
            )
            if value is not None
        }
        if len(identity_values) != 1:
            raise OwnerReplyReadinessError("owner_binding_unavailable")
        identities[expected_id] = _provider_id(next(iter(identity_values)))
    if identities[account_id] == identities[other_id] or recipient_chat_id != identities[other_id]:
        raise OwnerReplyReadinessError("scope_denied")
    connection = connection_by_account[account_id]

    canonical_rows = (
        await session.execute(
            select(Workspace.id, TransportConnector.id.label("connector_id"))
            .join(TransportConnector, TransportConnector.workspace_id == Workspace.id)
            .where(
                Workspace.key == scope.workspace_id,
                Workspace.is_active.is_(True),
                TransportConnector.connector_key == TelegramConnectorKey.BUSINESS.value,
                TransportConnector.connector_kind == "telegram_business",
                TransportConnector.is_enabled.is_(True),
            )
            .limit(2)
        )
    ).all()
    if len(canonical_rows) != 1:
        raise OwnerReplyReadinessError("canonical_binding_unavailable")
    workspace_id = canonical_rows[0].id

    # Deliberately broader than current Mayak admission: ANY active installation
    # on this private provider dialog blocks seed, regardless of profile/revision.
    binding_count = int(
        await session.scalar(
            select(func.count(AgentInstallationModel.id))
            .join(
                Dialog,
                and_(
                    Dialog.id == AgentInstallationModel.dialog_id,
                    Dialog.workspace_id == AgentInstallationModel.workspace_id,
                    Dialog.connector_id == AgentInstallationModel.connector_id,
                ),
            )
            .join(
                TransportConnector,
                and_(
                    TransportConnector.id == Dialog.connector_id,
                    TransportConnector.workspace_id == Dialog.workspace_id,
                ),
            )
            .where(
                Dialog.workspace_id == workspace_id,
                Dialog.provider_dialog_id == recipient_chat_id,
                Dialog.dialog_kind == "private",
                TransportConnector.connector_kind == "telegram_business",
                AgentInstallationModel.state == "active",
            )
        )
        or 0
    )

    conversations = (
        await session.execute(
            select(Conversation.id, Conversation.last_inbound_at)
            .where(
                Conversation.account_id == account_id,
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Conversation.chat_id == recipient_chat_id,
                Conversation.business_connection_id == connection.business_connection_id,
            )
            .limit(2)
        )
    ).all()
    if len(conversations) > 1:
        raise OwnerReplyReadinessError("ambiguous_owner_dialog")
    task: dict[str, Any] | None = None
    if conversations:
        conversation = conversations[0]
        predicates = [
            ReplyTask.conversation_id == conversation.id,
            ReplyTask.account_id == account_id,
            ReplyTask.account_id == Conversation.account_id,
            ReplyTask.project_id == Conversation.project_id,
            ReplyTask.project_id.in_(scope.project_ids),
            Message.account_id == ReplyTask.account_id,
            Message.project_id == ReplyTask.project_id,
            Message.bot_id == Conversation.bot_id,
            Message.direction == "inbound",
            Message.chat_id == recipient_chat_id,
            Message.sender_id == recipient_chat_id,
            Message.business_connection_id == connection.business_connection_id,
        ]
        if provider_message_id is not None:
            predicates.append(Message.telegram_message_id == int(provider_message_id))
        task_rows = (
            await session.execute(
                select(
                    ReplyTask.id,
                    ReplyTask.status,
                    Message.canonical_message_id,
                )
                .join(Conversation, Conversation.id == ReplyTask.conversation_id)
                .join(Message, Message.id == ReplyTask.trigger_message_id)
                .where(*predicates)
                .order_by(
                    case((ReplyTask.status == "needs_reply", 0), else_=1),
                    ReplyTask.created_at.desc(),
                    ReplyTask.id.desc(),
                )
                .limit(2 if provider_message_id is not None else 1)
            )
        ).all()
        if len(task_rows) > 1:
            raise OwnerReplyReadinessError("ambiguous_owner_dialog")
        if task_rows:
            row = task_rows[0]
            if row.status not in {"needs_reply", "replied", "dismissed", "snoozed"}:
                raise OwnerReplyReadinessError("owner_binding_unavailable")
            if provider_message_id is not None:
                pending = await session.scalar(
                    select(ReplyTask.id)
                    .where(
                        ReplyTask.conversation_id == conversation.id,
                        ReplyTask.account_id == account_id,
                        ReplyTask.project_id == Conversation.project_id,
                        ReplyTask.project_id.in_(scope.project_ids),
                        ReplyTask.status == "needs_reply",
                    )
                    .join(Conversation, Conversation.id == ReplyTask.conversation_id)
                    .order_by(ReplyTask.created_at.desc(), ReplyTask.id.desc())
                    .limit(1)
                )
                if pending is not None and pending != row.id:
                    raise OwnerReplyReadinessError("task_superseded")
            receipt_processed: bool | None = None
            turn_count: int | None = None
            if provider_message_id is not None and row.canonical_message_id is not None:
                receipt_rows = (
                    await session.execute(
                        select(CanonicalMessage.id, TelegramReceipt.status.label("receipt_status"))
                        .join(
                            Dialog,
                            and_(
                                Dialog.id == CanonicalMessage.dialog_id,
                                Dialog.workspace_id == CanonicalMessage.workspace_id,
                            ),
                        )
                        .outerjoin(
                            TelegramReceipt, TelegramReceipt.id == CanonicalMessage.receipt_id
                        )
                        .where(
                            CanonicalMessage.id == row.canonical_message_id,
                            CanonicalMessage.workspace_id == workspace_id,
                            CanonicalMessage.direction == "inbound",
                            CanonicalMessage.provider_message_id == provider_message_id,
                            CanonicalMessage.provider_sender_id == recipient_chat_id,
                            Dialog.dialog_kind == "private",
                            Dialog.connector_id == canonical_rows[0].connector_id,
                            Dialog.provider_dialog_id == recipient_chat_id,
                        )
                        .limit(1)
                    )
                ).all()
                if receipt_rows:
                    receipt_processed = receipt_rows[0].receipt_status == "processed"
                    turn_count = int(
                        await session.scalar(
                            select(func.count(AgentTurnModel.id)).where(
                                AgentTurnModel.workspace_id == workspace_id,
                                AgentTurnModel.trigger_message_id == row.canonical_message_id,
                            )
                        )
                        or 0
                    )
            last_inbound = conversation.last_inbound_at
            task = {
                "task_id": str(row.id),
                "status": row.status,
                "follow_up_required": row.status == "replied",
                "has_inbound_within_24h": bool(
                    last_inbound is not None
                    and observed - timedelta(hours=24) < last_inbound <= observed
                ),
                "exact_provider_message_match": provider_message_id is not None,
                "receipt_processed": receipt_processed,
                "correlated_agent_turn_count": turn_count,
                "mayak_reason_code_persisted": False,
            }
    return {
        "status": "task_found" if task is not None else "no_task",
        "account_alias": account_alias,
        "recipient_chat_id": recipient_chat_id,
        "observed_at": observed.isoformat(),
        "connection_enabled": True,
        "stored_can_reply": connection.can_reply is True,
        "task": task,
        "seed_observation": {
            "state": "unbound" if binding_count == 0 else "blocked",
            "active_binding_count": binding_count,
            "eligible_observed": bool(
                binding_count == 0
                and DEFAULT_PROJECT_ID in scope.project_ids
                and by_id[account_id].send_enabled
                and connection.can_reply is True
            ),
            "valid_for_seconds": 60,
            "guarantees_zero_provider_calls": False,
        },
        "reply_sequence": await build_reply_sequence_readiness(
            session=session,
            principal=principal,
            worker_registry=worker_registry,
            now=observed,
        ),
    }


async def build_reply_sequence_readiness(
    *,
    session: AsyncSession,
    principal: Principal,
    worker_registry: WorkerHealthRegistry | None,
    now: datetime,
) -> dict[str, Any]:
    """Aggregate granted sequence metadata without loading draft/source content."""
    with session.no_autoflush:
        return await _reply_sequence_readiness(
            session=session,
            principal=principal,
            worker_registry=worker_registry,
            now=now,
        )


async def _reply_sequence_readiness(
    *,
    session: AsyncSession,
    principal: Principal,
    worker_registry: WorkerHealthRegistry | None,
    now: datetime,
) -> dict[str, Any]:
    scope = principal.access_scope
    if not scope.is_explicit or not ({"gate:inbox:read", "gate:status:read"} & principal.scopes):
        raise OwnerReplyReadinessError("scope_denied")
    metadata = ReplyDraft.prompt_context["reply_sequence"]
    sequence_id = metadata["id"].as_string()
    state = metadata["state"].as_string()
    workspace = metadata["workspace_id"].as_string()
    # The draft's grants define inclusion. Inconsistent related rows or JSON
    # must remain visible as aggregate anomalies, never erase a scoped draft.
    malformed_task = or_(
        ReplyTask.id.is_(None),
        ReplyTask.account_id != ReplyDraft.account_id,
        ReplyTask.project_id != ReplyDraft.project_id,
        ReplyTask.conversation_id != ReplyDraft.conversation_id,
    )
    malformed_conversation = or_(
        Conversation.id.is_(None),
        Conversation.account_id != ReplyDraft.account_id,
        Conversation.project_id != ReplyDraft.project_id,
    )
    malformed_attempt = and_(
        SendAttempt.id.is_not(None),
        or_(
            SendAttempt.account_id != ReplyDraft.account_id,
            SendAttempt.project_id != ReplyDraft.project_id,
            SendAttempt.conversation_id != ReplyDraft.conversation_id,
            SendAttempt.reply_task_id != ReplyDraft.reply_task_id,
        ),
    )
    malformed = or_(
        sequence_id.is_(None),
        sequence_id == "",
        state.is_(None),
        state.not_in(("scheduled", "sent", "stopped", "cancelled", "failed", "unknown")),
        workspace.is_(None),
        workspace != scope.workspace_id,
        malformed_task,
        malformed_conversation,
        malformed_attempt,
    )
    pending = and_(state == "scheduled", SendAttempt.id.is_(None))
    result = (
        await session.execute(
            select(
                func.count(case((pending, 1))).label("pending_items"),
                func.count(distinct(case((pending, sequence_id)))).label("pending_sequences"),
                func.count(
                    case((SendAttempt.status.in_(("reserved", "sending", "unknown")), 1))
                ).label("unresolved_attempts"),
                func.count(case((malformed, 1))).label("malformed_items"),
            )
            .select_from(ReplyDraft)
            .outerjoin(ReplyTask, ReplyTask.id == ReplyDraft.reply_task_id)
            .outerjoin(Conversation, Conversation.id == ReplyDraft.conversation_id)
            .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
            .where(
                ReplyDraft.account_id.in_(scope.account_ids),
                ReplyDraft.project_id.in_(scope.project_ids),
                metadata.as_string().is_not(None),
            )
        )
    ).one()
    return {
        "worker": reply_sequence_worker_metadata(worker_registry, now=now),
        "pending_items": int(result.pending_items),
        "pending_sequences": int(result.pending_sequences),
        "unresolved_attempts": int(result.unresolved_attempts),
        "malformed_items": int(result.malformed_items),
    }
