# ruff: noqa
"""Minimal read-only MCP foundation for Gate."""

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
        return _jsonrpc_result(request_id=request_id, result={"tools": [_gate_status_tool()]})
    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict):
            return _jsonrpc_error(
                request_id=request_id,
                code=-32602,
                message="Invalid params",
            )
        tool_name = params.get("name")
        if tool_name != "gate_get_status":
            return _jsonrpc_error(
                request_id=request_id,
                code=-32601,
                message=f"Unknown tool: {tool_name}",
            )
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
            "tools": ["gate_get_status"],
            "writes_enabled": False,
            "send_enabled": False,
            "auth": "oauth_planned_not_implemented",
        },
    }


async def _count(*, session: AsyncSession, model: Any, where: Any | None = None) -> int:
    query = select(func.count(model.id))
    if where is not None:
        query = query.where(where)
    result = await session.execute(query)
    return int(result.scalar_one())


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


def _jsonrpc_result(*, request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(*, request_id: Any, code: int, message: str) -> JsonObject:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
