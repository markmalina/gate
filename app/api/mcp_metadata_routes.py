# ruff: noqa
"""MCP protected resource metadata endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings

router = APIRouter()
settings_dependency = Depends(get_settings)

JsonObject = dict[str, Any]

MCP_RESOURCE_SCOPES = [
    "gate:status:read",
    "gate:accounts:read",
    "gate:projects:read",
    "gate:inbox:read",
    "gate:conversations:read",
    "gate:routing:read",
    "gate:routing:write",
    "gate:drafts:read",
    "gate:drafts:write",
    "gate:tasks:write",
    "gate:imports:read",
    "gate:messages:send",
]

MCP_READ_ONLY_SCOPES = [
    "gate:status:read",
    "gate:inbox:read",
]


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata(
    request: Request,
    settings: Settings = settings_dependency,
) -> JsonObject:
    """Return OAuth protected resource metadata for Gate MCP."""

    resource = _public_origin(request)
    return {
        "resource": resource,
        "authorization_servers": _authorization_servers(resource),
        "scopes_supported": MCP_RESOURCE_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{resource}/docs",
        "mcp": _mcp_metadata(settings=settings),
    }


@router.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_mcp_metadata(
    request: Request,
    settings: Settings = settings_dependency,
) -> JsonObject:
    """Return MCP-specific protected resource metadata."""

    resource = _public_origin(request)
    return {
        "resource": f"{resource}/mcp",
        "authorization_servers": _authorization_servers(resource),
        "scopes_supported": MCP_RESOURCE_SCOPES,
        "bearer_methods_supported": ["header"],
        "mcp": _mcp_metadata(settings=settings),
    }


@router.get("/mcp/metadata")
async def mcp_metadata(
    request: Request,
    settings: Settings = settings_dependency,
) -> JsonObject:
    """Return a human/debug friendly MCP metadata snapshot."""

    resource = _public_origin(request)
    return {
        "service": "gate",
        "resource": f"{resource}/mcp",
        "commit": _commit(settings),
        "auth": {
            "type": "oauth2.1_planned",
            "enforced": False,
            "protected_resource_metadata": f"{resource}/.well-known/oauth-protected-resource/mcp",
            "authorization_servers": _authorization_servers(resource),
            "scopes_supported": MCP_RESOURCE_SCOPES,
            "current_read_only_scopes": MCP_READ_ONLY_SCOPES,
        },
        "tools": {
            "read_only": ["gate_get_status", "gate_list_unanswered"],
            "write": [],
            "send": [],
        },
    }


def _mcp_metadata(*, settings: Settings) -> JsonObject:
    return {
        "service": "gate",
        "endpoint": "/mcp",
        "protocol_version": "2025-06-18",
        "commit": _commit(settings),
        "auth_enforced": False,
        "auth_status": "metadata_published_oauth_not_enforced",
        "read_only_tools": ["gate_get_status", "gate_list_unanswered"],
        "write_tools_enabled": False,
        "send_tools_enabled": False,
    }


def _authorization_servers(resource: str) -> list[str]:
    return [f"{resource}/.well-known/oauth-authorization-server"]


def _commit(settings: Settings) -> str:
    return settings.deployment_sha[:12] if settings.deployment_sha != "unknown" else "unknown"


def _public_origin(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")
