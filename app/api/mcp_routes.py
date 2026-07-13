# mypy: ignore-errors
# ruff: noqa
"""Scoped Gate MCP endpoint for inbox, drafts and explicit reply sending."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import String, and_, case, cast, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.application.account_labels import account_label
from app.application.reply_drafts import (
    ReplyDraftError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.reply_sends import (
    ReplySendError,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
from app.core.config import Settings, get_settings
from app.core.mcp_oauth import McpOAuthError, authenticate_mcp_oauth
from app.core.mcp_security import (
    AccessScope,
    Principal,
    bearer_token_matches,
    build_mcp_read_principal,
)
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    Message,
    MessageDirection,
    PersonIdentity,
    Project,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

READ_ONLY_TOOL_ANNOTATIONS: JsonObject = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
MCP_TOOL_NAMES = [
    "gate_get_status",
    "gate_list_unanswered",
    "gate_get_reply_context",
    "gate_save_reply_draft",
    "gate_send_approved_reply",
]
MCP_AUTH_MODE_TEMPORARY_BEARER = "temporary_bearer"
MCP_AUTH_MODE_OAUTH = "oauth"
ANSWERED_MESSAGE_SOURCES = ("telegram", "gate-approved-draft")
MCP_TOOL_SCOPES = {
    "gate_get_status": "gate:status:read",
    "gate_list_unanswered": "gate:inbox:read",
    "gate_get_reply_context": "gate:inbox:read",
    "gate_save_reply_draft": "gate:drafts:write",
    "gate_send_approved_reply": "gate:messages:send",
}


@router.post("/mcp")
async def mcp_endpoint(
    payload: JsonObject,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle contained reads and the provider-neutral ChatGPT OAuth contract."""

    _require_mcp_ready(settings)
    request_id = payload.get("id")
    method = payload.get("method")
    oauth_discovery = settings.mcp_auth_mode == MCP_AUTH_MODE_OAUTH
    if method == "initialize":
        if not oauth_discovery:
            await _require_mcp_principal(
                authorization=authorization,
                settings=settings,
                required_scope="gate:status:read",
            )
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {
                    "name": "gate",
                    "version": _commit(settings),
                },
                "capabilities": {"tools": {}},
            },
        )
    if method == "tools/list":
        if not oauth_discovery:
            await _require_mcp_principal(
                authorization=authorization,
                settings=settings,
                required_scope="gate:status:read",
            )
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "tools": [
                    _gate_status_tool(settings),
                    _gate_list_unanswered_tool(settings),
                    _gate_get_reply_context_tool(settings),
                    _gate_save_reply_draft_tool(settings),
                    _gate_send_approved_reply_tool(settings),
                ]
            },
        )
    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict):
            return _jsonrpc_error(request_id=request_id, code=-32602, message="Invalid params")
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if tool_name not in MCP_TOOL_SCOPES:
            return _jsonrpc_error(
                request_id=request_id,
                code=-32601,
                message=f"Unknown tool: {tool_name}",
            )
        try:
            principal = await _require_mcp_principal(
                authorization=authorization,
                settings=settings,
                required_scope=MCP_TOOL_SCOPES[tool_name],
                session=session,
            )
        except McpOAuthError as exc:
            return _oauth_required_tool_result(
                request_id=request_id,
                settings=settings,
                scope=MCP_TOOL_SCOPES[tool_name],
                error=exc.error,
                description=exc.description,
            )
        if tool_name == "gate_get_status":
            snapshot = await _build_gate_status(
                session=session,
                settings=settings,
                principal=principal,
            )
            return _tool_result(snapshot, _format_status_text(snapshot), request_id)
        if tool_name == "gate_list_unanswered":
            inbox = await _build_unanswered_list(
                session=session,
                arguments=arguments,
                principal=principal,
            )
            return _tool_result(inbox, _format_unanswered_text(inbox), request_id)
        if tool_name == "gate_get_reply_context":
            return await _get_reply_context_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_save_reply_draft":
            return await _save_reply_draft_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_send_approved_reply":
            return await _send_approved_reply_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                telegram=telegram,
                settings=settings,
            )
    if method == "notifications/initialized":
        if not oauth_discovery:
            await _require_mcp_principal(
                authorization=authorization,
                settings=settings,
                required_scope="gate:status:read",
            )
        return {"ok": True}
    return _jsonrpc_error(
        request_id=request_id,
        code=-32601,
        message=f"Method not found: {method}",
    )


def _require_mcp_ready(settings: Settings) -> None:
    if not settings.gate_mcp_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if settings.mcp_auth_mode not in {
        MCP_AUTH_MODE_TEMPORARY_BEARER,
        MCP_AUTH_MODE_OAUTH,
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unsupported MCP authentication mode",
        )
    if settings.mcp_auth_mode == MCP_AUTH_MODE_OAUTH and not all(
        (
            settings.mcp_authorization_server,
            settings.mcp_public_resource_url,
            settings.mcp_oauth_jwks_url,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP OAuth validation is not configured",
        )


async def _require_mcp_principal(
    *,
    authorization: str | None,
    settings: Settings,
    required_scope: str,
    session: AsyncSession | None = None,
) -> Principal:
    if settings.mcp_auth_mode == MCP_AUTH_MODE_OAUTH:
        principal = await authenticate_mcp_oauth(
            authorization=authorization,
            settings=settings,
            required_scope=required_scope,
        )
        if principal.access_scope.is_explicit:
            return principal
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MCP workspace access resolver requires a database session",
            )
        return await _resolve_workspace_principal(
            session=session,
            principal=principal,
        )
    if settings.mcp_auth_mode != MCP_AUTH_MODE_TEMPORARY_BEARER:
        raise HTTPException(status_code=503, detail="Unsupported MCP authentication mode")
    if not bearer_token_matches(authorization, settings.mcp_read_bearer_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP bearer authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = build_mcp_read_principal(
            workspace_id=settings.mcp_read_workspace_id,
            account_grants=settings.mcp_read_account_ids,
            project_grants=settings.mcp_read_project_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP access scope is not configured",
        ) from exc
    if required_scope not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP bearer token does not grant the required scope",
        )
    return principal


async def _resolve_workspace_principal(
    *,
    session: AsyncSession,
    principal: Principal,
) -> Principal:
    """Resolve current Gate grants from workspace-owned database records."""

    workspace_id = principal.access_scope.workspace_id
    account_result = await session.execute(
        select(Account.id).where(
            Account.owner == workspace_id,
            Account.is_active.is_(True),
            Account.read_enabled.is_(True),
        )
    )
    account_ids = frozenset(account_result.scalars())
    if not account_ids:
        raise McpOAuthError(
            error="insufficient_scope",
            description="The Gate workspace has no active readable accounts",
        )

    project_grants = union_all(
        select(Account.linked_project_id.label("project_id")).where(
            Account.id.in_(account_ids),
            Account.linked_project_id.is_not(None),
        ),
        select(Message.project_id.label("project_id")).where(
            Message.account_id.in_(account_ids),
        ),
        select(ReplyTask.project_id.label("project_id")).where(
            ReplyTask.account_id.in_(account_ids),
        ),
        select(ReplyDraft.project_id.label("project_id")).where(
            ReplyDraft.account_id.in_(account_ids),
        ),
    ).subquery()
    project_result = await session.execute(
        select(Project.id).where(
            Project.id.in_(select(project_grants.c.project_id)),
            Project.is_active.is_(True),
        )
    )
    project_ids = frozenset(project_result.scalars())
    if not project_ids:
        raise McpOAuthError(
            error="insufficient_scope",
            description="The Gate workspace has no active projects",
        )

    return Principal(
        subject=principal.subject,
        auth_method=principal.auth_method,
        scopes=principal.scopes,
        access_scope=AccessScope(
            workspace_id=workspace_id,
            account_ids=account_ids,
            project_ids=project_ids,
        ),
    )


async def _build_gate_status(
    *, session: AsyncSession, settings: Settings, principal: Principal
) -> JsonObject:
    accounts = principal.access_scope.account_ids
    projects = principal.access_scope.project_ids
    owner_telegram_ids = _owner_telegram_ids_subquery(accounts)
    counts = {
        "accounts": await _count(session=session, model=Account, where=(Account.id.in_(accounts),)),
        "business_connections": await _count(
            session=session,
            model=TelegramBusinessConnection,
            where=(TelegramBusinessConnection.account_id.in_(accounts),),
        ),
        "active_business_connections": await _count(
            session=session,
            model=TelegramBusinessConnection,
            where=(
                TelegramBusinessConnection.account_id.in_(accounts),
                TelegramBusinessConnection.is_enabled.is_(True),
            ),
        ),
        "conversations": await _count(
            session=session,
            model=Conversation,
            where=(Conversation.account_id.in_(accounts), Conversation.project_id.in_(projects)),
        ),
        "messages": await _count(
            session=session,
            model=Message,
            where=(Message.account_id.in_(accounts), Message.project_id.in_(projects)),
        ),
        "reply_tasks": await _count(
            session=session,
            model=ReplyTask,
            where=(ReplyTask.account_id.in_(accounts), ReplyTask.project_id.in_(projects)),
        ),
        "unanswered": await _count_unanswered(
            session=session,
            accounts=accounts,
            projects=projects,
        ),
        "answered_messages": await _count(
            session=session,
            model=Message,
            where=(
                Message.account_id.in_(accounts),
                Message.project_id.in_(projects),
                Message.direction == MessageDirection.OUTBOUND.value,
                Message.chat_id.not_in(select(owner_telegram_ids.c.owner_id)),
                _answered_message_filter(),
            ),
        ),
        "drafts": await _count(
            session=session,
            model=ReplyDraft,
            where=(ReplyDraft.account_id.in_(accounts), ReplyDraft.project_id.in_(projects)),
        ),
    }
    connection_result = await session.execute(
        select(Account, TelegramBusinessConnection)
        .outerjoin(
            TelegramBusinessConnection,
            TelegramBusinessConnection.account_id == Account.id,
        )
        .where(Account.id.in_(accounts))
        .order_by(
            Account.alias.asc(),
            TelegramBusinessConnection.is_enabled.desc(),
            TelegramBusinessConnection.last_update_at.desc(),
        )
    )
    latest_connections: dict[UUID, tuple[Account, TelegramBusinessConnection | None]] = {}
    for account, connection in connection_result.all():
        latest_connections.setdefault(account.id, (account, connection))
    connection_health = [
        _serialize_connection_health(account=account, connection=connection)
        for account, connection in latest_connections.values()
    ]
    contact_directory = await _build_contact_directory_status(
        session=session,
        accounts=accounts,
    )
    return {
        "service": "gate",
        "environment": settings.gate_env,
        "commit": _commit(settings),
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
        "send_enabled": settings.gate_send_enabled,
        "access": {
            "subject": principal.subject,
            "workspace_id": principal.access_scope.workspace_id,
            "account_count": len(accounts),
            "project_count": len(projects),
        },
        "counts": counts,
        "connection_health": connection_health,
        "contact_directory": contact_directory,
        "mcp": {
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tools": MCP_TOOL_NAMES,
            "draft_writes_enabled": True,
            "send_enabled": settings.gate_send_enabled,
            "send_requires_explicit_confirmation": True,
            "auth": (
                "temporary_bearer_enforced"
                if principal.auth_method == "bearer"
                else f"{principal.auth_method}_enforced"
            ),
        },
    }


async def _build_contact_directory_status(
    *, session: AsyncSession, accounts: frozenset[UUID]
) -> list[JsonObject]:
    contact_counts = (
        select(
            PersonIdentity.account_id.label("account_id"),
            func.count(PersonIdentity.id).label("imported_contacts"),
        )
        .where(
            PersonIdentity.account_id.in_(accounts),
            PersonIdentity.platform == "telegram",
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .is_not(None),
        )
        .group_by(PersonIdentity.account_id)
        .subquery()
    )
    ranked_imports = (
        select(
            AuditLog.account_id.label("account_id"),
            AuditLog.created_at.label("created_at"),
            AuditLog.metadata_["source"].as_string().label("source"),
            func.row_number()
            .over(
                partition_by=AuditLog.account_id,
                order_by=[AuditLog.created_at.desc(), AuditLog.id.desc()],
            )
            .label("rank"),
        )
        .where(
            AuditLog.account_id.in_(accounts),
            AuditLog.action == "telegram_contact_snapshot_imported",
        )
        .subquery()
    )
    result = await session.execute(
        select(
            Account,
            func.coalesce(contact_counts.c.imported_contacts, 0),
            ranked_imports.c.created_at,
            ranked_imports.c.source,
        )
        .outerjoin(contact_counts, contact_counts.c.account_id == Account.id)
        .outerjoin(
            ranked_imports,
            and_(
                ranked_imports.c.account_id == Account.id,
                ranked_imports.c.rank == 1,
            ),
        )
        .where(Account.id.in_(accounts))
        .order_by(Account.alias.asc())
    )
    return [
        {
            "account": _serialize_account(account),
            "imported_contacts": int(imported_contacts),
            "last_source": source,
            "last_import_at": (
                _serialize_datetime(last_import_at)
                if last_import_at is not None
                else None
            ),
        }
        for account, imported_contacts, last_import_at, source in result.all()
    ]


async def _build_unanswered_list(
    *, session: AsyncSession, arguments: JsonObject, principal: Principal
) -> JsonObject:
    limit = _bounded_limit(arguments.get("limit"), default=10, maximum=50)
    accounts = principal.access_scope.account_ids
    projects = principal.access_scope.project_ids
    owner_telegram_ids = _owner_telegram_ids_subquery(accounts)
    ranked_tasks = (
        select(
            ReplyTask.id.label("task_id"),
            func.row_number()
            .over(
                partition_by=ReplyTask.conversation_id,
                order_by=(ReplyTask.created_at.desc(), ReplyTask.id.desc()),
            )
            .label("conversation_rank"),
            func.count(ReplyTask.id)
            .over(partition_by=ReplyTask.conversation_id)
            .label("pending_message_count"),
        )
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            ReplyTask.account_id.in_(accounts),
            ReplyTask.project_id.in_(projects),
            Conversation.account_id.in_(accounts),
            Conversation.project_id.in_(projects),
            Conversation.requires_reply.is_(True),
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.INBOUND.value,
            or_(
                Conversation.last_outbound_at.is_(None),
                func.coalesce(Message.received_at, Message.created_at)
                > Conversation.last_outbound_at,
            ),
            _non_service_message_filter(),
        )
        .subquery()
    )
    result = await session.execute(
        select(
            ReplyTask,
            Conversation,
            Message,
            TelegramUpdate,
            Account,
            ranked_tasks.c.pending_message_count,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .options(undefer(ReplyTask.account_id))
        .join(ranked_tasks, ranked_tasks.c.task_id == ReplyTask.id)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .join(Account, Message.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == Message.sender_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(ranked_tasks.c.conversation_rank == 1)
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    items = [
        _serialize_unanswered_item(
            task=task,
            conversation=conversation,
            message=message,
            telegram_update=telegram_update,
            account=account,
            pending_message_count=pending_message_count,
            resolved_contact_name=resolved_contact_name,
        )
        for (
            task,
            conversation,
            message,
            telegram_update,
            account,
            pending_message_count,
            resolved_contact_name,
        ) in result.all()
    ]

    answered_result = await session.execute(
        select(
            Message,
            TelegramUpdate,
            Account,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .join(Account, Message.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id
                == func.coalesce(Message.recipient_id, Message.chat_id),
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.chat_id.not_in(select(owner_telegram_ids.c.owner_id)),
            _answered_message_filter(),
        )
        .order_by(
            func.coalesce(Message.sent_at, Message.created_at).desc(),
            Message.id.desc(),
        )
        .limit(limit)
    )
    answered_items = [
        _serialize_answered_item(
            message=message,
            telegram_update=telegram_update,
            account=account,
            resolved_contact_name=resolved_contact_name,
        )
        for message, telegram_update, account, resolved_contact_name in answered_result.all()
    ]
    return {
        "items": items,
        "count": len(items),
        "answered_items": answered_items,
        "answered_count": len(answered_items),
        "limit": limit,
        "next_cursor": None,
    }


async def _get_reply_context_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_task_id = arguments.get("task_id")
    if not isinstance(raw_task_id, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id is required",
        )
    try:
        task_id = UUID(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id must be a UUID",
        )
    limit = _bounded_limit(arguments.get("limit"), default=10, maximum=20)
    scope = principal.access_scope
    task_result = await session.execute(
        select(
            ReplyTask,
            Conversation,
            Message,
            TelegramUpdate,
            Account,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .options(undefer(ReplyTask.account_id))
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .join(Account, ReplyTask.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == Message.sender_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            ReplyTask.id == task_id,
            ReplyTask.account_id.in_(scope.account_ids),
            ReplyTask.project_id.in_(scope.project_ids),
            Conversation.account_id.in_(scope.account_ids),
            Conversation.project_id.in_(scope.project_ids),
            Message.account_id.in_(scope.account_ids),
            Message.project_id.in_(scope.project_ids),
        )
    )
    row = task_result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="task_not_found",
            message="Reply task not found in the allowed scope",
        )
    (
        task,
        conversation,
        trigger,
        trigger_update,
        account,
        resolved_contact_name,
    ) = row
    history_counterparty_user_id = case(
        (
            Message.direction == MessageDirection.INBOUND.value,
            Message.sender_id,
        ),
        else_=func.coalesce(Message.recipient_id, Message.chat_id),
    )
    history_result = await session.execute(
        select(
            Message,
            TelegramUpdate,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == history_counterparty_user_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            Message.account_id == conversation.account_id,
            Message.project_id == conversation.project_id,
            Message.chat_id == conversation.chat_id,
            Message.business_connection_id.is_not_distinct_from(
                conversation.business_connection_id
            ),
        )
        .order_by(
            func.coalesce(
                Message.sent_at,
                Message.received_at,
                Message.created_at,
            ).desc(),
            Message.id.desc(),
        )
        .limit(limit)
    )
    history_rows = list(reversed(history_result.all()))
    content = {
        "task_id": str(task.id),
        "task_ref": str(task.id)[:8],
        "status": task.status,
        "reply_required": task.status == ReplyTaskStatus.NEEDS_REPLY.value,
        "account": _serialize_account(account),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "counterparty": _counterparty(
            message=trigger,
            telegram_update=trigger_update,
            resolved_contact_name=resolved_contact_name,
            fallback_name=getattr(conversation, "title", None),
        ),
        "messages": [
            {
                **_serialize_message(message),
                "counterparty": _counterparty(
                    message=message,
                    telegram_update=telegram_update,
                    resolved_contact_name=history_contact_name,
                    fallback_name=getattr(conversation, "title", None),
                ),
            }
            for message, telegram_update, history_contact_name in history_rows
        ],
        "count": len(history_rows),
        "limit": limit,
    }
    return _tool_result(
        content,
        _format_reply_context_text(content),
        request_id,
    )


async def _count_unanswered(
    *,
    session: AsyncSession,
    accounts: frozenset[UUID],
    projects: frozenset[UUID],
) -> int:
    result = await session.execute(
        select(func.count(func.distinct(ReplyTask.conversation_id)))
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            ReplyTask.account_id.in_(accounts),
            ReplyTask.project_id.in_(projects),
            Conversation.account_id.in_(accounts),
            Conversation.project_id.in_(projects),
            Conversation.requires_reply.is_(True),
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.INBOUND.value,
            or_(
                Conversation.last_outbound_at.is_(None),
                func.coalesce(Message.received_at, Message.created_at)
                > Conversation.last_outbound_at,
            ),
            _non_service_message_filter(),
        )
    )
    return int(result.scalar_one())


async def _count(
    *, session: AsyncSession, model: Any, where: tuple[Any, ...] = ()
) -> int:
    query = select(func.count(model.id)).where(*where)
    result = await session.execute(query)
    return int(result.scalar_one())


def _serialize_unanswered_item(
    *,
    task: ReplyTask,
    conversation: Conversation,
    message: Message,
    telegram_update: TelegramUpdate | None,
    account: Account,
    pending_message_count: int,
    resolved_contact_name: str | None,
) -> JsonObject:
    task_id = str(task.id)
    return {
        "task_id": task_id,
        "task_ref": task_id[:8],
        "status": task.status,
        "priority": task.priority,
        "reason": task.reason,
        "pending_message_count": int(pending_message_count),
        "account_id": str(task.account_id),
        "project_id": str(task.project_id),
        "account": _serialize_account(account),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "counterparty": _counterparty(
            message=message,
            telegram_update=telegram_update,
            resolved_contact_name=resolved_contact_name,
            fallback_name=getattr(conversation, "title", None),
        ),
        "message": _serialize_message(message),
        "created_at": _serialize_datetime(task.created_at),
    }


def _serialize_answered_item(
    *,
    message: Message,
    telegram_update: TelegramUpdate | None,
    account: Account,
    resolved_contact_name: str | None,
) -> JsonObject:
    return {
        "account_id": str(message.account_id),
        "project_id": str(message.project_id),
        "account": _serialize_account(account),
        "counterparty": _counterparty(
            message=message,
            telegram_update=telegram_update,
            resolved_contact_name=resolved_contact_name,
        ),
        "message": _serialize_message(message),
    }


def _serialize_account(account: Account) -> JsonObject:
    return {
        "id": str(account.id),
        "alias": account.alias,
        "label": account_label(
            alias=account.alias,
            country_code=account.country_code,
        ),
    }


def _serialize_connection_health(
    *,
    account: Account,
    connection: TelegramBusinessConnection | None,
) -> JsonObject:
    if connection is None:
        state = "missing"
        can_reply = False
    else:
        can_reply = connection.rights.get("can_reply") is True
        if not connection.is_enabled:
            state = "inactive"
        elif not can_reply:
            state = "cannot_reply"
        elif connection.last_error:
            state = "degraded"
        else:
            state = "ready"
    return {
        "account": _serialize_account(account),
        "state": state,
        "connected": connection is not None and connection.is_enabled,
        "can_reply": can_reply,
        "last_checked_at": (
            _serialize_datetime(connection.last_update_at) if connection is not None else None
        ),
        "last_error": connection.last_error if connection is not None else None,
    }


def _serialize_message(message: Message) -> JsonObject:
    return {
        "id": str(message.id),
        "direction": message.direction,
        "source": message.source,
        "chat_id": message.chat_id,
        "sender_id": message.sender_id,
        "recipient_id": message.recipient_id,
        "text_preview": _truncate_text(message.text or "", 240),
        "created_at": _serialize_datetime(message.created_at),
        "received_at": _serialize_datetime(message.received_at),
        "sent_at": _serialize_datetime(message.sent_at),
    }


def _counterparty(
    *,
    message: Message,
    telegram_update: TelegramUpdate | None,
    resolved_contact_name: str | None = None,
    fallback_name: str | None = None,
) -> JsonObject:
    payload = telegram_update.payload if telegram_update is not None else {}
    telegram_message = _telegram_message_payload(payload)
    raw_person = (
        telegram_message.get("from")
        if message.direction == MessageDirection.INBOUND.value
        else telegram_message.get("chat")
    )
    profile = raw_person if isinstance(raw_person, dict) else {}
    first_name = _optional_text(profile.get("first_name"))
    last_name = _optional_text(profile.get("last_name"))
    title = _optional_text(profile.get("title"))
    username = _optional_text(profile.get("username"))
    profile_name = " ".join(
        part for part in (first_name, last_name) if part
    ) or title
    snapshot = _counterparty_snapshot(message)
    snapshot_name = _optional_text(snapshot.get("display_name"))
    imported_name = _optional_text(resolved_contact_name)
    display_name = imported_name or snapshot_name or profile_name or fallback_name
    platform_user_id = _optional_text(profile.get("id")) or (
        message.sender_id
        if message.direction == MessageDirection.INBOUND.value
        else message.recipient_id or message.chat_id
    )
    label = display_name or (f"@{username}" if username else None) or platform_user_id
    return {
        "platform": "telegram",
        "platform_user_id": platform_user_id,
        "display_name": display_name,
        "username": username,
        "label": label,
    }


def _counterparty_snapshot(message: Message) -> JsonObject:
    payload = getattr(message, "payload", {})
    if not isinstance(payload, dict):
        return {}
    value = payload.get("_gate_counterparty")
    return value if isinstance(value, dict) else {}

def _telegram_message_payload(payload: Any) -> JsonObject:
    if not isinstance(payload, dict):
        return {}
    for key in ("business_message", "edited_business_message", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _owner_telegram_ids_subquery(accounts: frozenset[UUID]) -> Any:
    owner_names = select(Account.owner).where(Account.id.in_(accounts))
    return union_all(
        select(cast(Account.telegram_user_id, String).label("owner_id")).where(
            Account.owner.in_(owner_names),
            Account.telegram_user_id.is_not(None),
        ),
        select(
            cast(TelegramBusinessConnection.telegram_user_id, String).label("owner_id")
        )
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            Account.owner.in_(owner_names),
            TelegramBusinessConnection.telegram_user_id.is_not(None),
        ),
        select(
            cast(TelegramBusinessConnection.user_chat_id, String).label("owner_id")
        )
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            Account.owner.in_(owner_names),
            TelegramBusinessConnection.user_chat_id.is_not(None),
        ),
    ).subquery()


def _answered_message_filter() -> Any:
    return and_(
        Message.source.in_(ANSWERED_MESSAGE_SOURCES),
        Message.text.is_not(None),
        func.length(func.trim(Message.text)) > 0,
        _non_service_message_filter(),
    )


def _non_service_message_filter() -> Any:
    return or_(
        Message.text.is_(None),
        and_(
            Message.text.notilike("/start bizChat%"),
            Message.text.notilike("/webhook%"),
            Message.text.notilike("/events%"),
            Message.text.notilike("/messages%"),
            Message.text.notilike("/raw%"),
            Message.text.notilike("/status%"),
            Message.text.notilike("/inbox%"),
            Message.text.notilike("/draft%"),
            Message.text.notilike("/drafts%"),
            Message.text.notilike("/done%"),
            Message.text.notilike("/dismiss%"),
            Message.text.notilike("/snooze%"),
            Message.text.notilike("/reopen%"),
            Message.text.notilike("/business%"),
            Message.text.notilike("Commit:%"),
            Message.text.notilike("TEST-%"),
        ),
    )


def _gate_status_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_get_status",
        "title": "Get Gate status",
        "description": "Return a scoped read-only Gate status snapshot.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:status:read")


def _gate_list_unanswered_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_list_unanswered",
        "title": "List unanswered Gate tasks",
        "description": (
            "Return scoped unanswered inbound tasks and a separate list of recent "
            "answered/outbound messages with Telegram counterparty context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}
            },
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:inbox:read")


def _gate_get_reply_context_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_get_reply_context",
        "title": "Get Gate reply context",
        "description": (
            "Return the scoped RU/BY account, recipient and recent chronological "
            "message history for one reply task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:inbox:read")


def _gate_save_reply_draft_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_save_reply_draft",
        "title": "Save a Gate reply draft",
        "description": (
            "Save proposed text as a scoped draft for an unanswered task. "
            "This tool never approves or sends the message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "text": {"type": "string", "minLength": 1, "maxLength": 4096},
            },
            "required": ["task_id", "text"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:drafts:write")


def _gate_send_approved_reply_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_send_approved_reply",
        "title": "Send an explicitly approved Gate reply",
        "description": (
            "Send the exact saved draft through its originating RU/BY Telegram "
            "Business account. Call only after showing the account, recipient and "
            "final text to Mark and receiving explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "format": "uuid"},
                "confirmed": {"type": "boolean", "const": True},
                "expected_account": {
                    "type": "string",
                    "enum": ["RU", "BY"],
                },
                "expected_recipient_chat_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                },
            },
            "required": [
                "draft_id",
                "confirmed",
                "expected_account",
                "expected_recipient_chat_id",
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:messages:send",
    )


async def _save_reply_draft_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_task_id = arguments.get("task_id")
    raw_text = arguments.get("text")
    if not isinstance(raw_task_id, str) or not isinstance(raw_text, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id and text are required",
        )
    try:
        task_id = UUID(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id must be a UUID",
        )

    service = SaveReplyDraftService(SqlReplyDraftWriter(session))
    try:
        saved = await service.execute(
            SaveReplyDraftCommand(principal=principal, task_id=task_id, text=raw_text)
        )
    except ReplyDraftError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    await session.commit()
    content = {
        "draft_id": str(saved.draft_id),
        "draft_ref": str(saved.draft_id)[:8],
        "task_id": str(saved.task_id),
        "account_id": str(saved.account_id),
        "project_id": str(saved.project_id),
        "account": {
            "alias": saved.account_alias,
            "label": saved.account_label,
        },
        "recipient_chat_id": saved.recipient_chat_id,
        "status": saved.status,
        "text": saved.text,
        "sent": False,
        "approval_required": True,
        "send_available": True,
        "confirmation": {
            "expected_account": saved.account_label,
            "expected_recipient_chat_id": saved.recipient_chat_id,
        },
    }
    return _tool_result(
        content,
        (
            f"Draft saved: {content['draft_ref']}\n"
            f"Account: {saved.account_label}\n"
            f"Recipient chat: {saved.recipient_chat_id}\n"
            "Not sent. Show the final text and ask Mark for explicit confirmation."
        ),
        request_id,
    )


async def _send_approved_reply_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
) -> JsonObject:
    raw_draft_id = arguments.get("draft_id")
    confirmed = arguments.get("confirmed")
    expected_account = arguments.get("expected_account")
    expected_recipient = arguments.get("expected_recipient_chat_id")
    if (
        not isinstance(raw_draft_id, str)
        or not isinstance(confirmed, bool)
        or not isinstance(expected_account, str)
        or not isinstance(expected_recipient, str)
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "draft_id, confirmed, expected_account and "
                "expected_recipient_chat_id are required"
            ),
        )
    try:
        draft_id = UUID(raw_draft_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="draft_id must be a UUID",
        )

    service = SendApprovedReplyService(
        SqlTelegramReplySender(
            session=session,
            telegram=telegram,
            settings=settings,
        )
    )
    try:
        sent = await service.execute(
            SendApprovedReplyCommand(
                principal=principal,
                draft_id=draft_id,
                confirmed=confirmed,
                expected_account=expected_account,
                expected_recipient_chat_id=expected_recipient,
            )
        )
    except ReplySendError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )

    content = {
        "attempt_id": str(sent.attempt_id),
        "attempt_ref": str(sent.attempt_id)[:8],
        "draft_id": str(sent.draft_id),
        "task_id": str(sent.task_id),
        "account_id": str(sent.account_id),
        "project_id": str(sent.project_id),
        "account": {
            "alias": sent.account_alias,
            "label": sent.account_label,
        },
        "recipient_chat_id": sent.recipient_chat_id,
        "status": sent.status,
        "sent": sent.sent,
        "idempotent_replay": sent.idempotent_replay,
        "provider_message_id": sent.provider_message_id,
        "error_code": sent.error_code,
        "error_detail": sent.error_detail,
    }
    if sent.sent:
        text = (
            f"Reply sent through {sent.account_label} to chat "
            f"{sent.recipient_chat_id}. Attempt: {str(sent.attempt_id)[:8]}."
        )
    else:
        text = (
            f"Reply was not confirmed as sent. Status: {sent.status}. "
            f"Account: {sent.account_label}. Attempt: {str(sent.attempt_id)[:8]}. "
            "Do not retry this draft automatically."
        )
    return _tool_result(content, text, request_id)


def _with_security_scheme(*, tool: JsonObject, settings: Settings, scope: str) -> JsonObject:
    if settings.mcp_auth_mode != MCP_AUTH_MODE_OAUTH:
        return tool
    schemes = [{"type": "oauth2", "scopes": [scope]}]
    tool["securitySchemes"] = schemes
    tool["_meta"] = {"securitySchemes": schemes}
    return tool


def _oauth_required_tool_result(
    *,
    request_id: Any,
    settings: Settings,
    scope: str,
    error: str = "invalid_token",
    description: str = "A valid Gate access token is required",
) -> JsonObject:
    challenge = _oauth_challenge(
        settings=settings,
        scope=scope,
        error=error,
        description=description,
    )
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": "Authentication required: connect Gate to continue.",
                }
            ],
            "_meta": {"mcp/www_authenticate": [challenge]},
            "isError": True,
        },
    )


def _oauth_challenge(
    *, settings: Settings, scope: str, error: str, description: str
) -> str:
    resource = settings.mcp_public_resource_url.rstrip("/")
    origin = resource.removesuffix("/mcp")
    metadata_url = f"{origin}/.well-known/oauth-protected-resource/mcp"
    return (
        f'Bearer resource_metadata="{metadata_url}", scope="{scope}", '
        f'error="{error}", '
        f'error_description="{description}"'
    )


def _tool_result(content: JsonObject, text: str, request_id: Any) -> JsonObject:
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": text}],
            "structuredContent": content,
            "isError": False,
        },
    )


def _tool_error_result(*, request_id: Any, code: str, message: str) -> JsonObject:
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": message}],
            "structuredContent": {"error": code},
            "isError": True,
        },
    )


def _format_status_text(snapshot: JsonObject) -> str:
    counts = snapshot["counts"]
    lines = [
        f"Gate status — {snapshot['environment']}",
        f"Commit: {snapshot['commit']}",
        f"Bot: {snapshot['bot_username']}",
        f"Mode: {snapshot['default_mode']}",
        f"Send enabled: {snapshot['send_enabled']}",
        f"Workspace: {snapshot['access']['workspace_id']}",
        "",
        "Telegram Business connections:",
    ]
    for item in snapshot["connection_health"]:
        lines.append(
            f"- {item['account']['label']}: {item['state']}; "
            f"can_reply={item['can_reply']}"
        )
    lines.extend(["", "Contact directory:"])
    for item in snapshot["contact_directory"]:
        source = item["last_source"] or "none"
        last_import_at = item["last_import_at"] or "never"
        lines.append(
            f"- {item['account']['label']}: imported={item['imported_contacts']}; "
            f"source={source}; last_import_at={last_import_at}"
        )
    lines.extend(["", "Counts:"])
    lines.extend(f"- {name}: {value}" for name, value in counts.items())
    return "\n".join(lines)

def _format_reply_context_text(context: JsonObject) -> str:
    account = context["account"]
    counterparty = context["counterparty"]
    lines = [
        f"Gate reply context — [{account['label']}] {counterparty['label']}",
        f"task: {context['task_ref']}",
        f"chat: {context['conversation']['chat_id']}",
        "",
        f"Recent messages: {context['count']}",
    ]
    for message in context["messages"]:
        direction = "IN" if message["direction"] == MessageDirection.INBOUND.value else "OUT"
        lines.extend(
            [
                "",
                f"[{direction}] {message['text_preview'] or '<empty>'}",
            ]
        )
    return "\n".join(lines)


def _format_unanswered_text(inbox: JsonObject) -> str:
    lines = [f"Unanswered Gate conversations: {inbox['count']}"]
    if not inbox["items"]:
        lines.append("No unanswered inbound messages.")
    for index, item in enumerate(inbox["items"], start=1):
        lines.extend(
            [
                "",
                f"{index}. [{item['account']['label']}] "
                f"{item['counterparty']['label']}: "
                f"{item['message']['text_preview'] or '<empty>'}",
                f"pending messages: {item['pending_message_count']}",
                f"task: {item['task_ref']}",
                f"chat: {item['conversation']['chat_id']}",
            ]
        )

    lines.extend(["", f"Answered/outbound Gate messages: {inbox['answered_count']}"])
    if not inbox["answered_items"]:
        lines.append("No answered/outbound messages.")
    for index, item in enumerate(inbox["answered_items"], start=1):
        lines.extend(
            [
                "",
                f"{index}. [{item['account']['label']}] "
                f"{item['counterparty']['label']}: "
                f"{item['message']['text_preview'] or '<empty>'}",
                f"chat: {item['message']['chat_id']}",
            ]
        )
    return "\n".join(lines)


def _bounded_limit(value: Any, *, default: int, maximum: int) -> int:
    if not isinstance(value, int):
        return default
    if value < 1:
        return 1
    return min(value, maximum)


def _truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _commit(settings: Settings) -> str:
    return settings.deployment_sha[:12] if settings.deployment_sha != "unknown" else "unknown"


def _jsonrpc_result(*, request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(*, request_id: Any, code: int, message: str) -> JsonObject:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
