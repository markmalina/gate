"""Shared JSON-RPC response envelopes for the Gate MCP API."""

import json
from typing import Any

from fastapi import HTTPException, Request

JsonObject = dict[str, Any]
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"


def jsonrpc_result(*, request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def tool_result(content: JsonObject, text: str, request_id: Any) -> JsonObject:
    return jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": text}],
            "structuredContent": content,
            "isError": False,
        },
    )


def tool_error_result(
    *,
    request_id: Any,
    code: str,
    message: str,
    details: JsonObject | None = None,
    processing_history: JsonObject | None = None,
) -> JsonObject:
    context: JsonObject = {"error": code, **(details or {})}
    if processing_history is not None:
        context["processing_history"] = processing_history
    return jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": message}],
            "structuredContent": context,
            "isError": True,
        },
    )


def bounded_limit(value: Any, *, default: int, maximum: int) -> int:
    if not isinstance(value, int):
        return default
    if value < 1:
        return 1
    return min(value, maximum)


async def read_mcp_payload(request: Request, *, limit: int) -> JsonObject:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isascii() or not declared.isdecimal() or len(declared) > 20:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if int(declared) > limit:
            raise HTTPException(status_code=413, detail="MCP request is too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="MCP request is too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON-RPC body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON-RPC body must be an object")
    return payload


def jsonrpc_error(*, request_id: Any, code: int, message: str) -> JsonObject:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
