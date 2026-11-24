"""Shared JSON-RPC response envelopes for the Gate MCP API."""

from typing import Any

JsonObject = dict[str, Any]
JSONRPC_VERSION = "2.0"


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
