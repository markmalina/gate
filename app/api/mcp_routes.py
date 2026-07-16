# mypy: ignore-errors
# ruff: noqa
"""Scoped Gate MCP endpoint for inbox, drafts and explicit reply sending."""

from typing import Annotated, Any

import httpx
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.api.routes import get_telegram_client
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import AlphaLifecycleError, AlphaLifecycleService
from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    alpha_model_readiness_payload,
)
from app.application.alpha_owner_control import (
    AlphaOwnerAction,
    AlphaOwnerControlError,
    ControlAlphaInstallationCommand,
    alpha_installation_payload,
    alpha_installations_payload,
    control_alpha_installation,
)
from app.application.alpha_shadow_reviews import alpha_shadow_reviews_payload
from app.application.mcp_inbox import (
    build_unanswered_list,
    counterparty as _counterparty,
    get_reply_context,
)
from app.application.mcp_status import (
    build_gate_status,
    serialize_connection_health as _serialize_connection_health,
)
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
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.infrastructure.alpha_model_preflight import (
    AlphaModelPreflightError,
    run_alpha_model_preflight,
)
from app.infrastructure.alpha_model_status import alpha_model_operational_status
from app.infrastructure.alpha_shadow_reviews import list_alpha_shadow_reviews
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.alpha_canary import (
    alpha_canary_audit_counts,
    alpha_canary_binding_count,
    alpha_canary_installation_count,
)
from app.models.domain import (
    Account,
    Message,
    MessageDirection,
    Project,
    ReplyDraft,
    ReplyTask,
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
    "gate_alpha_list_installations",
    "gate_alpha_control_installation",
    "gate_alpha_run_model_preflight",
    "gate_alpha_list_shadow_reviews",
]
MCP_AUTH_MODE_TEMPORARY_BEARER = "temporary_bearer"
MCP_AUTH_MODE_OAUTH = "oauth"
MCP_TOOL_SCOPES = {
    "gate_get_status": "gate:status:read",
    "gate_list_unanswered": "gate:inbox:read",
    "gate_get_reply_context": "gate:inbox:read",
    "gate_save_reply_draft": "gate:drafts:write",
    "gate_send_approved_reply": "gate:messages:send",
    "gate_alpha_list_installations": "gate:status:read",
    "gate_alpha_control_installation": "gate:agents:manage",
    "gate_alpha_run_model_preflight": "gate:agents:manage",
    "gate_alpha_list_shadow_reviews": "gate:agents:manage",
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
                    _gate_alpha_list_installations_tool(settings),
                    _gate_alpha_control_installation_tool(settings),
                    _gate_alpha_run_model_preflight_tool(settings),
                    _gate_alpha_list_shadow_reviews_tool(settings),
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
            snapshot = await build_gate_status(
                session=session,
                settings=settings,
                principal=principal,
                protocol_version=MCP_PROTOCOL_VERSION,
                tool_names=MCP_TOOL_NAMES,
            )
            snapshot["alpha_canary"] = await _build_alpha_canary_status(
                session=session,
                settings=settings,
                workspace_key=principal.access_scope.workspace_id,
            )
            return _tool_result(snapshot, _format_status_text(snapshot), request_id)
        if tool_name == "gate_alpha_list_installations":
            return await _alpha_list_installations_tool_result(
                request_id=request_id,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_alpha_list_shadow_reviews":
            return await _alpha_list_shadow_reviews_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_alpha_control_installation":
            return await _alpha_control_installation_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_alpha_run_model_preflight":
            return await _alpha_model_preflight_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
            )
        if tool_name == "gate_list_unanswered":
            inbox = await build_unanswered_list(
                session=session,
                scope=principal.access_scope,
                limit=_bounded_limit(arguments.get("limit"), default=10, maximum=50),
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

    context = await get_reply_context(
        session=session,
        scope=principal.access_scope,
        task_id=task_id,
        limit=_bounded_limit(arguments.get("limit"), default=10, maximum=20),
    )
    if context is None:
        return _tool_error_result(
            request_id=request_id,
            code="task_not_found",
            message="Reply task not found in the allowed scope",
        )
    return _tool_result(
        context,
        _format_reply_context_text(context),
        request_id,
    )


async def _alpha_list_installations_tool_result(
    *,
    request_id: Any,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installations = await service.list_status(
            principal.access_scope.workspace_id
        )
    except AlphaLifecycleError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    payload = alpha_installations_payload(installations)
    lines = [f"Alpha installations: {payload['count']}"]
    for item in payload["items"]:
        lines.append(
            f"- {item['group_label']}: {item['state']}; "
            f"actions={','.join(item['available_actions']) or 'none'}"
        )
    return _tool_result(payload, "\n".join(lines), request_id)


async def _alpha_list_shadow_reviews_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_limit = arguments.get("limit", 20)
    if (
        not isinstance(raw_limit, int)
        or isinstance(raw_limit, bool)
        or not 1 <= raw_limit <= 50
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="limit must be an integer between 1 and 50",
        )
    reviews = await list_alpha_shadow_reviews(
        session,
        workspace_key=principal.access_scope.workspace_id,
        limit=raw_limit,
    )
    payload = alpha_shadow_reviews_payload(reviews)
    lines = [
        f"Alpha shadow reviews: {payload['count']}",
        "Telegram delivery: blocked",
    ]
    for item in payload["items"]:
        lines.append(
            f"- {item['group_label']}: {item['status']}; "
            f"outcome={item['outcome'] or 'pending'}"
        )
    return _tool_result(payload, "\n".join(lines), request_id)


async def _alpha_control_installation_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    raw_action = arguments.get("action")
    raw_state = arguments.get("expected_state")
    if (
        not isinstance(raw_ref, str)
        or not isinstance(raw_action, str)
        or not isinstance(raw_state, str)
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "installation_ref, action, expected_state and confirmed=true "
                "are required"
            ),
        )
    try:
        action = AlphaOwnerAction(raw_action)
        expected_state = AlphaInstallationState(raw_state)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Unsupported Alpha action or expected state",
        )
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installation = await control_alpha_installation(
            service,
            ControlAlphaInstallationCommand(
                workspace_key=principal.access_scope.workspace_id,
                installation_ref=raw_ref,
                action=action,
                expected_state=expected_state,
                confirmed=True,
            ),
        )
        await session.commit()
    except (AlphaLifecycleError, AlphaOwnerControlError, ValueError) as exc:
        await session.rollback()
        code = getattr(exc, "code", "alpha_owner_control_failed")
        return _tool_error_result(
            request_id=request_id,
            code=code,
            message=code,
        )
    payload = {
        "action": action.value,
        "installation": alpha_installation_payload(installation),
    }
    return _tool_result(
        payload,
        f"Alpha {payload['installation']['group_label']}: "
        f"{payload['installation']['state']}",
        request_id,
    )


async def _alpha_model_preflight_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if arguments.get("confirmed") is not True:
        return _tool_error_result(
            request_id=request_id,
            code="alpha_provider_preflight_confirmation_required",
            message="confirmed=true is required",
        )
    try:
        async with httpx.AsyncClient() as http_client:
            preflight = await run_alpha_model_preflight(
                session,
                settings=settings,
                http_client=http_client,
                workspace_key=principal.access_scope.workspace_id,
            )
    except AlphaModelPreflightError as exc:
        await session.rollback()
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.code,
        )
    payload = {"preflight": preflight}
    return _tool_result(
        payload,
        f"Alpha provider preflight: {preflight['state']}",
        request_id,
    )


def _gate_status_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_get_status",
        "title": "Get Gate status",
        "description": "Return a scoped read-only Gate status snapshot.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_get_status"],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:status:read")


def _gate_alpha_list_installations_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_list_installations",
        "title": "List Alpha groups",
        "description": (
            "List owner-visible Alpha installations by group label and opaque "
            "reference; never exposes Telegram group IDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_list_installations"
        ],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:status:read",
    )


def _gate_alpha_list_shadow_reviews_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_list_shadow_reviews",
        "title": "Review Alpha shadow proposals",
        "description": (
            "List model-backed Alpha proposals from approved groups for owner "
            "review. It never exposes Telegram IDs and never delivers messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                }
            },
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_list_shadow_reviews"
        ],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:agents:manage",
    )


def _gate_alpha_control_installation_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_control_installation",
        "title": "Approve, pause or resume Alpha group",
        "description": (
            "Apply one confirmed approve/pause/resume action to the exact "
            "installation reference returned by Gate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "action": {
                    "type": "string",
                    "enum": ["approve", "pause", "resume"],
                },
                "expected_state": {
                    "type": "string",
                    "enum": ["pending_approval", "active", "paused"],
                },
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": [
                "installation_ref",
                "action",
                "expected_state",
                "confirmed",
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_control_installation"
        ],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:agents:manage",
    )


def _gate_alpha_run_model_preflight_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_run_model_preflight",
        "title": "Run Alpha provider preflight",
        "description": (
            "Run the one-shot synthetic OpenAI preflight only after billing is "
            "confirmed ready. It never sends group content or Telegram messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": ["confirmed"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_run_model_preflight"
        ],
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
        scope="gate:agents:manage",
    )


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
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_list_unanswered"],
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
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"],
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
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_save_reply_draft"],
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
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_send_approved_reply"],
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
    alpha = snapshot["alpha_canary"]
    lines.extend(
        [
            "",
            "Alpha canary:",
            f"- connector_enabled: {alpha['connector_enabled']}",
            f"- webhook_credentials_configured: "
            f"{alpha['webhook_credentials_configured']}",
            f"- one_group_discovered: {alpha['one_group_discovered']}",
            f"- one_active_exact_revision: "
            f"{alpha['one_active_exact_revision']}",
            f"- delivery_enabled: {alpha['delivery_enabled']}",
            f"- accepted_messages: {alpha['audit']['canonical_messages']}",
            f"- agent_turns: {alpha['audit']['agent_turns']}",
            f"- delivery_grants: {alpha['audit']['delivery_grants']}",
            f"- send_attempts: {alpha['audit']['send_attempts']}",
            "",
            "Alpha model shadow:",
            f"- state: {alpha['model_shadow']['state']}",
            f"- staged_deployments: "
            f"{alpha['model_shadow']['staged_deployments']}",
            f"- provider_preflight_verified: "
            f"{alpha['model_shadow']['provider_preflight_verified']}",
            f"- model_turns: {alpha['model_shadow']['model_turns']}",
            f"- failed_turns: {alpha['model_shadow']['failed_turns']}",
            f"- running_turns: {alpha['model_shadow']['running_turns']}",
            f"- model_delivery_grants: "
            f"{alpha['model_shadow']['model_delivery_grants']}",
            f"- stuck_provider_preflights: "
            f"{alpha['model_shadow']['stuck_provider_preflights']}",
            f"- provider_error_category: "
            f"{alpha['model_shadow']['last_provider_error_category']}",
            f"- promotion_next: "
            f"{alpha['model_shadow']['promotion_dry_run']['next_stage']}",
            f"- promotion_blocked_by: "
            f"{','.join(alpha['model_shadow']['promotion_dry_run']['blocked_by']) or 'none'}",
            f"- budget_reserved: $"
            f"{alpha['model_shadow']['budget']['reserved_microusd'] / 1_000_000:.6f}",
            f"- budget_remaining: $"
            f"{alpha['model_shadow']['budget']['remaining_microusd'] / 1_000_000:.6f}",
        ]
    )
    return "\n".join(lines)


async def _build_alpha_canary_status(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_key: str,
) -> JsonObject:
    installation_count = await alpha_canary_installation_count(
        session,
        workspace_key=workspace_key,
    )
    active_binding_count = await alpha_canary_binding_count(
        session,
        workspace_key=workspace_key,
    )
    audit_counts = await alpha_canary_audit_counts(
        session,
        workspace_key=workspace_key,
    )
    model = await alpha_model_operational_status(
        session,
        workspace_key=workspace_key,
    )
    model_shadow = alpha_model_readiness_payload(
        AlphaModelReadinessFacts(
            runtime_enabled=settings.gate_alpha_model_runtime_enabled,
            data_gate_approved=settings.gate_alpha_model_data_gate_approved,
            api_key_configured=bool(settings.openai_api_key.get_secret_value()),
            staged_deployments=model.staged_deployments,
            monthly_budget_microusd=model.monthly_budget_microusd,
            reserved_microusd=model.reserved_microusd,
            model_turns=model.model_turns,
            proposed_turns=model.proposed_turns,
            failed_turns=model.failed_turns,
            queued_turns=model.queued_turns,
            running_turns=model.running_turns,
            model_delivery_grants=model.model_delivery_grants,
            stuck_provider_preflights=model.stuck_provider_preflights,
            last_provider_error_code=model.last_provider_error_code,
            fixture_binding_active=active_binding_count == 1,
            provider_preflight_successes=model.provider_preflight_successes,
            provider_preflight_failures=model.provider_preflight_failures,
        )
    )
    return {
        "telegram_worker_enabled": settings.gate_telegram_worker_enabled,
        "connector_enabled": settings.gate_alpha_connector_enabled,
        "shadow_worker_enabled": settings.gate_alpha_shadow_worker_enabled,
        "canary_enabled": settings.gate_alpha_canary_enabled,
        "delivery_enabled": settings.gate_alpha_delivery_enabled,
        "privacy_mode_confirmed": settings.gate_alpha_privacy_mode_confirmed,
        "webhook_credentials_configured": bool(
            settings.telegram_alpha_bot_token.get_secret_value()
            and settings.telegram_alpha_bot_username
            and settings.telegram_alpha_webhook_secret.get_secret_value()
        ),
        "one_group_discovered": installation_count == 1,
        "one_active_exact_revision": active_binding_count == 1,
        "audit": audit_counts,
        "model_shadow": model_shadow,
    }

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
