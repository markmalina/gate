# mypy: ignore-errors
# ruff: noqa
"""Minimal read-only MCP foundation for Gate."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import (
    Account,
    Conversation,
    Message,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
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
MCP_TOOL_NAMES = ["gate_get_status", "gate_list_unanswered"]


@router.post("/mcp")
async def mcp_endpoint(
    payload: JsonObject,
    _authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> JsonObject:
    """Handle a minimal read-only MCP JSON-RPC request."""

    request_id = payload.get("id")
    method = payload.get("method")
    if method == "initialize":
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {
                    "name": "gate",
                    "version": settings.deployment_sha[:12]
                    if settings.deployment_sha != "unknown"
                    else "unknown",
                },
                "capabilities": {"tools": {}},
            },
        )
    if method == "tools/list":
        return _jsonrpc_result(
            request_id=request_id,
            result={"tools": [_gate_status_tool(), _gate_list_unanswered_tool()]},
        )
    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict):
            return _jsonrpc_error(
                request_id=request_id,
                code=-32602,
                message="Invalid params",
            )
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if tool_name == "gate_get_status":
            status = await _build_gate_status(session=session, settings=settings)
            return _jsonrpc_result(
                request_id=request_id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": _format_status_text(status),
                        }
                    ],
                    "structuredContent": status,
                    "isError": False,
                },
            )
        if tool_name == "gate_list_unanswered":
            inbox = await _build_unanswered_list(session=session, arguments=arguments)
            return _jsonrpc_result(
                request_id=request_id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": _format_unanswered_text(inbox),
                        }
                    ],
                    "structuredContent": inbox,
                    "isError": False,
                },
            )
        return _jsonrpc_error(
            request_id=request_id,
            code=-32601,
            message=f"Unknown tool: {tool_name}",
        )
    if method == "notifications/initialized":
        return {"ok": True}
    return _jsonrpc_error(
        request_id=request_id,
        code=-32601,
        message=f"Method not found: {method}",
    )


async def _build_gate_status(*, session: AsyncSession, settings: Settings) -> JsonObject:
    accounts = await _count(session=session, model=Account)
    business_connections = await _count(session=session, model=TelegramBusinessConnection)
    active_business_connections = await _count(
        session=session,
        model=TelegramBusinessConnection,
        where=TelegramBusinessConnection.is_enabled.is_(True),
    )
    conversations = await _count(session=session, model=Conversation)
    messages = await _count(session=session, model=Message)
    reply_tasks = await _count(session=session, model=ReplyTask)
    unanswered = await _count(
        session=session,
        model=ReplyTask,
        where=ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
    )
    drafts = await _count(session=session, model=ReplyDraft)
    return {
        "service": "gate",
        "environment": settings.gate_env,
        "commit": settings.deployment_sha[:12]
        if settings.deployment_sha != "unknown"
        else "unknown",
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
        "send_enabled": settings.gate_send_enabled,
        "counts": {
            "accounts": accounts,
            "business_connections": business_connections,
            "active_business_connections": active_business_connections,
            "conversations": conversations,
            "messages": messages,
            "reply_tasks": reply_tasks,
            "unanswered": unanswered,
            "drafts": drafts,
        },
        "mcp": {
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tools": MCP_TOOL_NAMES,
            "writes_enabled": False,
            "send_enabled": False,
            "auth": "oauth_planned_not_implemented",
        },
    }


async def _build_unanswered_list(*, session: AsyncSession, arguments: JsonObject) -> JsonObject:
    limit = _bounded_limit(arguments.get("limit"), default=10, maximum=50)
    result = await session.execute(
        select(ReplyTask, Conversation, Message)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value)
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    items = [
        _serialize_unanswered_item(task=task, conversation=conversation, message=message)
        for task, conversation, message in result.all()
    ]
    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "next_cursor": None,
    }


async def _count(*, session: AsyncSession, model: Any, where: Any | None = None) -> int:
    query = select(func.count(model.id))
    if where is not None:
        query = query.where(where)
    result = await session.execute(query)
    return int(result.scalar_one())


def _serialize_unanswered_item(*, task: ReplyTask, conversation: Conversation, message: Message) -> JsonObject:
    task_id = str(task.id)
    message_text = message.text or ""
    return {
        "task_id": task_id,
        "task_ref": task_id[:8],
        "status": task.status,
        "priority": task.priority,
        "reason": task.reason,
        "account_id": str(getattr(task, "account_id", None) or getattr(conversation, "account_id", None) or ""),
        "project_id": str(getattr(task, "project_id", None) or getattr(conversation, "project_id", None) or ""),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "message": {
            "id": str(message.id),
            "chat_id": message.chat_id,
            "sender_id": message.sender_id,
            "text_preview": _truncate_text(message_text, 240),
            "created_at": _serialize_datetime(message.created_at),
            "received_at": _serialize_datetime(message.received_at),
        },
        "created_at": _serialize_datetime(task.created_at),
    }


def _gate_status_tool() -> JsonObject:
    return {
        "name": "gate_get_status",
        "title": "Get Gate status",
        "description": (
            "Return a read-only Gate status snapshot with deployment metadata "
            "and aggregate counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }


def _gate_list_unanswered_tool() -> JsonObject:
    return {
        "name": "gate_list_unanswered",
        "title": "List unanswered Gate tasks",
        "description": "Return read-only unanswered reply tasks with conversation and trigger message context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                }
            },
            "additionalProperties": False,
        },
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }


def _format_status_text(status: JsonObject) -> str:
    counts = status["counts"]
    return "\n".join(
        [
            f"Gate status — {status['environment']}",
            f"Commit: {status['commit']}",
            f"Bot: {status['bot_username']}",
            f"Mode: {status['default_mode']}",
            f"Send enabled: {status['send_enabled']}",
            "",
            "Counts:",
            f"- accounts: {counts['accounts']}",
            f"- business_connections: {counts['business_connections']}",
            f"- active_business_connections: {counts['active_business_connections']}",
            f"- conversations: {counts['conversations']}",
            f"- messages: {counts['messages']}",
            f"- reply_tasks: {counts['reply_tasks']}",
            f"- unanswered: {counts['unanswered']}",
            f"- drafts: {counts['drafts']}",
        ]
    )


def _format_unanswered_text(inbox: JsonObject) -> str:
    items = inbox["items"]
    if not items:
        return "No unanswered Gate tasks."
    lines = [f"Unanswered Gate tasks: {inbox['count']}"]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                "",
                f"{index}. {item['message']['text_preview'] or '<empty>'}",
                f"task: {item['task_ref']}",
                f"chat: {item['conversation']['chat_id']}",
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
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _jsonrpc_result(*, request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(*, request_id: Any, code: int, message: str) -> JsonObject:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
