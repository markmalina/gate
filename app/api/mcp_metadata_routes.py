# ruff: noqa
"""MCP protected-resource metadata and provider-neutral OAuth discovery."""

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
    """Return OAuth protected-resource metadata for the Gate MCP resource."""

    resource = _mcp_resource(request=request, settings=settings)
    return {
        "resource": resource,
        "authorization_servers": _authorization_servers(settings),
        "scopes_supported": MCP_RESOURCE_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{_resource_origin(resource)}/docs",
        "mcp": _mcp_metadata(settings=settings),
    }


@router.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_mcp_metadata(
    request: Request,
    settings: Settings = settings_dependency,
) -> JsonObject:
    """Return the canonical metadata document used by ChatGPT OAuth discovery."""

    resource = _mcp_resource(request=request, settings=settings)
    return {
        "resource": resource,
        "authorization_servers": _authorization_servers(settings),
        "scopes_supported": MCP_RESOURCE_SCOPES,
        "bearer_methods_supported": ["header"],
        "mcp": _mcp_metadata(settings=settings),
    }


@router.get("/mcp/metadata")
async def mcp_metadata(
    request: Request,
    settings: Settings = settings_dependency,
) -> JsonObject:
    """Return a human/debug-friendly MCP metadata snapshot."""

    resource = _mcp_resource(request=request, settings=settings)
    return {
        "service": "gate",
        "resource": resource,
        "commit": _commit(settings),
        "auth": {
            "type": "temporary_bearer_oauth2.1_planned",
            "mode": settings.mcp_auth_mode,
            "enabled": settings.gate_mcp_enabled,
            "enforced": settings.gate_mcp_enabled,
            "status": _auth_status(settings),
            "protected_resource_metadata": (
                f"{_resource_origin(resource)}/.well-known/oauth-protected-resource/mcp"
            ),
            "authorization_servers": _authorization_servers(settings),
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
        "enabled": settings.gate_mcp_enabled,
        "auth_mode": settings.mcp_auth_mode,
        "auth_enforced": settings.gate_mcp_enabled,
        "auth_status": _auth_status(settings),
        "read_only_tools": ["gate_get_status", "gate_list_unanswered"],
        "write_tools_enabled": False,
        "send_tools_enabled": False,
    }


def _auth_status(settings: Settings) -> str:
    if not settings.gate_mcp_enabled:
        return "disabled_by_default"
    if settings.mcp_auth_mode == "oauth":
        if settings.mcp_authorization_server and settings.mcp_public_resource_url:
            return "oauth_discovery_configured_token_validation_pending"
        return "oauth_configuration_incomplete"
    return "temporary_bearer_enforced"


def _authorization_servers(settings: Settings) -> list[str]:
    issuer = settings.mcp_authorization_server.strip().rstrip("/")
    return [issuer] if issuer else []


def _mcp_resource(*, request: Request, settings: Settings) -> str:
    configured = settings.mcp_public_resource_url.strip().rstrip("/")
    if configured:
        return configured
    return f"{_public_origin(request)}/mcp"


def _resource_origin(resource: str) -> str:
    return resource.removesuffix("/mcp")


def _commit(settings: Settings) -> str:
    return settings.deployment_sha[:12] if settings.deployment_sha != "unknown" else "unknown"


def _public_origin(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")
