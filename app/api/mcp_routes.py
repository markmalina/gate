# mypy: ignore-errors
# ruff: noqa
"""Contained read-only MCP endpoint for Gate."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import String, and_, cast, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.api.deps import get_db_session
from app.application.reply_drafts import (
    ReplyDraftError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.core.config import Settings, get_settings
from app.core.mcp_oauth import McpOAuthError, authenticate_mcp_oauth
from app.core.mcp_security import Principal, bearer_token_matches, build_mcp_read_principal
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.models.domain import (
    Account,
    Conversation,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
    TelegramUpdate,
)

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)

JsonObject = dict[str, Any]
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

READ_ONLY_TOOL_ANNOTATIONS: JsonObject = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
MCP_TOOL_NAMES = ["gate_get_status", "gate_list_unanswered", "gate_save_reply_draft"]
MCP_AUTH_MODE_TEMPORARY_BEARER = "temporary_bearer"
MCP_AUTH_MODE_OAUTH = "oauth"
ANSWERED_MESSAGE_SOURCES = ("telegram", "gate-approved-draft")
MCP_TOOL_SCOPES = {
    "gate_get_status": "gate:status:read",
    "gate_list_unanswered": "gate:inbox:read",
    "gate_save_reply_draft": "gate:drafts:write",
}


@router.post("/mcp")
async def mcp_endpoint(
    payload: JsonObject,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
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
                    _gate_save_reply_draft_tool(settings),
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
        if tool_name == "gate_save_reply_draft":
            return await _save_reply_draft_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
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
    *, authorization: str | None, settings: Settings, required_scope: str
) -> Principal:
    if settings.mcp_auth_mode == MCP_AUTH_MODE_OAUTH:
        return await authenticate_mcp_oauth(
            authorization=authorization,
            settings=settings,
            required_scope=required_scope,
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
        "mcp": {
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tools": MCP_TOOL_NAMES,
            "draft_writes_enabled": True,
            "send_enabled": False,
            "auth": (
                "temporary_bearer_enforced"
                if principal.auth_method == "bearer"
                else f"{principal.auth_method}_enforced"
            ),
        },
    }


async def _build_unanswered_list(
    *, session: AsyncSession, arguments: JsonObject, principal: Principal
) -> JsonObject:
    limit = _bounded_limit(arguments.get("limit"), default=10, maximum=50)
    accounts = principal.access_scope.account_ids
    projects = principal.access_scope.project_ids
    owner_telegram_ids = _owner_telegram_ids_subquery(accounts)
    result = await session.execute(
        select(ReplyTask, Conversation, Message, TelegramUpdate)
        .options(undefer(ReplyTask.account_id))
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            ReplyTask.account_id.in_(accounts),
            ReplyTask.project_id.in_(projects),
            Conversation.account_id.in_(accounts),
            Conversation.project_id.in_(projects),
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.INBOUND.value,
            _non_service_message_filter(),
        )
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    items = [
        _serialize_unanswered_item(
            task=task,
            conversation=conversation,
            message=message,
            telegram_update=telegram_update,
        )
        for task, conversation, message, telegram_update in result.all()
    ]

    answered_result = await session.execute(
        select(Message, TelegramUpdate)
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
        _serialize_answered_item(message=message, telegram_update=telegram_update)
        for message, telegram_update in answered_result.all()
    ]
    return {
        "items": items,
        "count": len(items),
        "answered_items": answered_items,
        "answered_count": len(answered_items),
        "limit": limit,
        "next_cursor": None,
    }


async def _count_unanswered(
    *,
    session: AsyncSession,
    accounts: frozenset[UUID],
    projects: frozenset[UUID],
) -> int:
    result = await session.execute(
        select(func.count(ReplyTask.id))
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            ReplyTask.account_id.in_(accounts),
            ReplyTask.project_id.in_(projects),
            Conversation.account_id.in_(accounts),
            Conversation.project_id.in_(projects),
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.INBOUND.value,
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
) -> JsonObject:
    task_id = str(task.id)
    return {
        "task_id": task_id,
        "task_ref": task_id[:8],
        "status": task.status,
        "priority": task.priority,
        "reason": task.reason,
        "account_id": str(task.account_id),
        "project_id": str(task.project_id),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "counterparty": _counterparty(
            message=message,
            telegram_update=telegram_update,
            fallback_name=getattr(conversation, "title", None),
        ),
        "message": _serialize_message(message),
        "created_at": _serialize_datetime(task.created_at),
    }


def _serialize_answered_item(
    *, message: Message, telegram_update: TelegramUpdate | None
) -> JsonObject:
    return {
        "account_id": str(message.account_id),
        "project_id": str(message.project_id),
        "counterparty": _counterparty(
            message=message,
            telegram_update=telegram_update,
        ),
        "message": _serialize_message(message),
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
    fallback_name: str | None = None,
) -> JsonObject:
    payload = telegram_update.payload if telegram_update is not None else {}
    telegram_message = _telegram_message_payload(payload)
    raw_person = (
        telegram_message.get("from")
        if message.direction == MessageDirection.INBOUND.value
        else telegram_message.get("chat")
    )
    person = raw_person if isinstance(raw_person, dict) else {}
    first_name = _optional_text(person.get("first_name"))
    last_name = _optional_text(person.get("last_name"))
    title = _optional_text(person.get("title"))
    username = _optional_text(person.get("username"))
    display_name = " ".join(
        part for part in (first_name, last_name) if part
    ) or title or fallback_name
    platform_user_id = _optional_text(person.get("id")) or (
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
        "status": saved.status,
        "text": saved.text,
        "sent": False,
        "approval_required": True,
        "send_available": False,
    }
    return _tool_result(
        content,
        (
            f"Draft saved: {content['draft_ref']}\n"
            "Not approved. Not sent. Sending is not available through MCP yet."
        ),
        request_id,
    )


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
    return "\n".join(
        [
            f"Gate status — {snapshot['environment']}",
            f"Commit: {snapshot['commit']}",
            f"Bot: {snapshot['bot_username']}",
            f"Mode: {snapshot['default_mode']}",
            f"Send enabled: {snapshot['send_enabled']}",
            f"Workspace: {snapshot['access']['workspace_id']}",
            "",
            "Counts:",
            *[f"- {name}: {value}" for name, value in counts.items()],
        ]
    )


def _format_unanswered_text(inbox: JsonObject) -> str:
    lines = [f"Unanswered inbound Gate tasks: {inbox['count']}"]
    if not inbox["items"]:
        lines.append("No unanswered inbound messages.")
    for index, item in enumerate(inbox["items"], start=1):
        lines.extend(
            [
                "",
                f"{index}. {item['counterparty']['label']}: "
                f"{item['message']['text_preview'] or '<empty>'}",
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
                f"{index}. {item['counterparty']['label']}: "
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
