"""Default-off personal command MCP contract; no provider or startup wiring."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

from fastapi import Request

from app.core.config import Settings
from app.core.mcp_oauth import McpOAuthError, authenticate_mcp_oauth
from app.personal_telegram.command_scope import CommandRuntimeError

JsonObject = dict[str, Any]
PERSONAL_COMMAND_SCOPES = {
    "gate_personal_command_readiness": "gate:personal:jobs:read",
    "gate_personal_command_prepare": "gate:personal:send_text",
    "gate_personal_command_execute": "gate:personal:send_text",
    "gate_personal_command_status": "gate:personal:jobs:read",
    "gate_personal_command_result": "gate:personal:jobs:read",
    "gate_personal_command_cancel": "gate:personal:jobs:cancel",
}

ERROR_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": ["error", "sent", "runtime_connected"],
    "properties": {
        "error": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,95}$"},
        "sent": {"const": False},
        "runtime_connected": {"const": False},
    },
}
OPERATION_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "operation_ref",
        "state",
        "receipt_ref",
        "error_code",
        "replay",
        "sent",
        "runtime_connected",
    ],
    "properties": {
        "operation_ref": {"type": "string", "pattern": "^operation_[0-9a-f]{32}$"},
        "state": {
            "enum": [
                "prepared",
                "queued",
                "running",
                "succeeded",
                "rejected",
                "cancelled",
                "expired",
                "unknown",
            ]
        },
        "receipt_ref": {"type": ["string", "null"]},
        "error_code": {"type": ["string", "null"]},
        "replay": {"type": "boolean"},
        "sent": {"const": False},
        "runtime_connected": {"const": False},
    },
}
READINESS_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ready", "runtime_connected", "blockers"],
    "properties": {
        "ready": {"const": False},
        "runtime_connected": {"const": False},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
}


def personal_command_tools(settings: Settings) -> list[JsonObject]:
    if not settings.gate_personal_command_tools_enabled:
        return []
    tools = []
    for name, scope in PERSONAL_COMMAND_SCOPES.items():
        action = name.removeprefix("gate_personal_command_")
        properties: JsonObject = {}
        if action == "prepare":
            properties = {
                "grant_ref": {"type": "string", "pattern": "^grant_[0-9a-f]{32}$"},
                "peer_ref": {"type": "string", "minLength": 16, "maxLength": 4096},
                "request_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9._:-]+$",
                },
                "text": {"type": "string", "minLength": 1, "maxLength": 4096},
            }
        elif action != "readiness":
            properties = {
                "operation_ref": {"type": "string", "pattern": "^operation_[0-9a-f]{32}$"}
            }
        security = [{"type": "oauth2", "scopes": [scope]}]
        tools.append(
            {
                "name": name,
                "description": (
                    f"Personal RU command {action}. Execute only queues a durable operation; "
                    "this staged contract has no Telegram dispatcher and sends no message. "
                    "Reuse the same request_key for retries of the same intent."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
                "outputSchema": {
                    "oneOf": [
                        READINESS_SCHEMA if action == "readiness" else OPERATION_SCHEMA,
                        ERROR_SCHEMA,
                    ]
                },
                "annotations": {
                    "readOnlyHint": action in {"readiness", "status", "result"},
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "securitySchemes": security,
                "_meta": {"securitySchemes": security},
            }
        )
    return tools


async def personal_command_call(
    *,
    request: Request | None,
    request_id: Any,
    tool_name: str,
    arguments: Any,
    authorization: str | None,
    settings: Settings,
) -> JsonObject:
    """Authenticate verified claims directly; never resolve broad Business grants."""
    if not settings.gate_personal_command_tools_enabled:
        return _error(request_id, "personal_command_disabled")
    if settings.mcp_auth_mode != "oauth":
        return _error(request_id, "personal_oauth_required")
    required_scope = PERSONAL_COMMAND_SCOPES[tool_name]
    try:
        principal = await authenticate_mcp_oauth(
            authorization=authorization, settings=settings, required_scope=required_scope
        )
    except McpOAuthError as exc:
        response = _error(request_id, exc.error)
        origin = settings.mcp_public_resource_url.rstrip("/").removesuffix("/mcp")
        response["result"]["_meta"] = {
            "mcp/www_authenticate": [
                f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp", '
                f'scope="{required_scope}", error="{exc.error}"'
            ]
        }
        return response
    action = tool_name.removeprefix("gate_personal_command_")
    if not _valid_arguments(action, arguments):
        return _error(request_id, "invalid_arguments")
    factory = (
        getattr(request.app.state, "personal_command_service_factory", None)
        if request is not None
        else None
    )
    if not callable(factory):
        return _error(request_id, "personal_command_unbound")
    try:
        service = factory(principal)
        if inspect.isawaitable(service):
            service = await service
        result = await getattr(service, action)(principal=principal, **arguments)
        return _result(request_id, result, is_error=False)
    except CommandRuntimeError as exc:
        code = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
            code = "personal_command_failed"
        return _error(request_id, code)
    except Exception:
        # Fail closed without provider/SQL/credential exception text in MCP results.
        return _error(request_id, "personal_command_failed")


def _valid_arguments(action: str, arguments: Any) -> bool:
    if not isinstance(arguments, dict):
        return False
    if action == "readiness":
        return not arguments
    if action != "prepare":
        return (
            set(arguments) == {"operation_ref"}
            and isinstance(arguments["operation_ref"], str)
            and re.fullmatch(r"operation_[0-9a-f]{32}", arguments["operation_ref"]) is not None
        )
    if set(arguments) != {"grant_ref", "peer_ref", "request_key", "text"}:
        return False
    if any(not isinstance(value, str) for value in arguments.values()):
        return False
    return (
        re.fullmatch(r"grant_[0-9a-f]{32}", arguments["grant_ref"]) is not None
        and 16 <= len(arguments["peer_ref"]) <= 4096
        and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", arguments["request_key"]) is not None
        and 1 <= len(arguments["text"]) <= 4096
    )


def _error(request_id: Any, code: str) -> JsonObject:
    return _result(
        request_id,
        {
            "error": code,
            "sent": False,
            "runtime_connected": False,
        },
        is_error=True,
    )


def _result(request_id: Any, content: JsonObject, *, is_error: bool) -> JsonObject:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
            "structuredContent": content,
            "isError": is_error,
        },
    }
