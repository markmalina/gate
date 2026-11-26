# mypy: ignore-errors
# ruff: noqa
"""Scoped Gate MCP endpoint for inbox, drafts and explicit reply sending."""

from typing import Annotated, Any

import httpx
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import (
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog_snapshots import CONTEXT_CHANGED_REPLY_TEXT, SNAPSHOT_PATTERN
from app.api.deps import get_db_session
from app.api.mcp_agent_reads import (
    _beta_export_space_checkpoint_tool_result,
    _beta_get_provider_probe_tool_result,
    _alpha_list_installations_tool_result,
    _alpha_get_webhook_status_tool_result,
    _alpha_list_shadow_reviews_tool_result,
    _alpha_get_test_group_presence_tool_result,
    _alpha_verify_review_replay_tool_result,
)
from app.api.mcp_reply_context import (
    _get_reply_context_tool_result,
    _format_reply_context_text,
)
from app.api.mcp_results import bounded_limit as _bounded_limit
from app.api.mcp_results import JSONRPC_VERSION, MCP_PROTOCOL_VERSION, read_mcp_payload
from app.api.mcp_results import jsonrpc_error as _jsonrpc_error
from app.api.mcp_results import jsonrpc_result as _jsonrpc_result
from app.api.mcp_results import tool_error_result as _tool_error_result
from app.api.mcp_results import tool_result as _tool_result
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.api.personal_command_mcp import (
    PERSONAL_COMMAND_SCOPES,
    personal_command_call,
    personal_command_tools,
)
from app.api.mcp_status import build_status_snapshot, format_status_text
from app.api.mcp_media import PREPARE_INPUT_SCHEMA, PREVIEW_INPUT_SCHEMA, media_tool_result
from app.api.health_routes import _get_worker_health_registry
from app.api.routes import get_telegram_client
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import AlphaLifecycleError, AlphaLifecycleService
from app.application.alpha_owner_control import (
    AlphaOwnerAction,
    AlphaOwnerControlError,
    ControlAlphaInstallationCommand,
    alpha_installation_payload,
    control_alpha_installation,
)
from app.application.mcp_inbox import (
    build_unanswered_list,
    counterparty as _counterparty,
    get_reply_context,
)
from app.application.mcp_status import serialize_connection_health as _serialize_connection_health
from app.application.telegram_rich import rich_content_schema
from app.application.reply_drafts import (
    ReplyDraftError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.owner_reply_readiness import (
    OWNER_REPLY_READINESS_INPUT_SCHEMA,
    OWNER_REPLY_READINESS_SCOPE,
    OWNER_REPLY_READINESS_TOOL_NAME,
    OwnerReplyReadinessError,
    build_owner_reply_readiness,
)
from app.application.document_formats import DOCUMENT_MIME_TYPES
from app.application.reply_documents import (
    PrepareReplyDocumentCommand,
    PrepareReplyDocumentService,
)
from app.application.reply_sends import (
    ReplySendError,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
from app.application.reply_sequences import (
    MAX_SEQUENCE_DRAFTS,
    MIN_SEQUENCE_DRAFTS,
    QueueApprovedReplySequenceCommand,
    QueueApprovedReplySequenceService,
    ReplySequenceError,
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
from app.infrastructure.alpha_owner_test_runtime import (
    AlphaOwnerTestRuntimeError,
    send_owner_test_review,
)
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_documents import SqlReplyDocumentWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.models.domain import (
    Account,
    Message,
    Project,
    ReplyDraft,
    ReplyTask,
    SendAttemptStatus,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]

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
    OWNER_REPLY_READINESS_TOOL_NAME,
    "gate_save_reply_draft",
    "gate_prepare_reply_document",
    "gate_prepare_reply_media",
    "gate_preview_reply_media",
    "gate_send_approved_reply",
    "gate_send_approved_document",
    "gate_queue_approved_reply_sequence",
    "gate_alpha_list_installations",
    "gate_alpha_get_webhook_status",
    "gate_alpha_control_installation",
    "gate_alpha_run_model_preflight",
    "gate_alpha_list_shadow_reviews",
    "gate_alpha_get_test_group_presence",
    "gate_alpha_send_review",
    "gate_alpha_verify_review_replay",
    "gate_beta_export_space_checkpoint",
    "gate_beta_get_provider_probe",
]
# Preserve the closed status schema used by previously registered clients.
# This is a supported core subset; tools/list is the exhaustive current catalog.
MCP_LEGACY_STATUS_TOOL_NAMES = [
    name for name in MCP_TOOL_NAMES if name not in {OWNER_REPLY_READINESS_TOOL_NAME, "gate_prepare_reply_media", "gate_preview_reply_media"}
]
MCP_AUTH_MODE_TEMPORARY_BEARER = "temporary_bearer"
MCP_AUTH_MODE_OAUTH = "oauth"
MCP_TOOL_SCOPES = {
    "gate_get_status": "gate:status:read",
    "gate_list_unanswered": "gate:inbox:read",
    "gate_get_reply_context": "gate:inbox:read",
    OWNER_REPLY_READINESS_TOOL_NAME: OWNER_REPLY_READINESS_SCOPE,
    "gate_save_reply_draft": "gate:drafts:write",
    "gate_prepare_reply_document": "gate:drafts:write",
    "gate_prepare_reply_media": "gate:drafts:write",
    "gate_preview_reply_media": "gate:inbox:read",
    "gate_send_approved_reply": "gate:messages:send",
    "gate_send_approved_document": "gate:messages:send",
    "gate_queue_approved_reply_sequence": "gate:messages:send",
    "gate_alpha_list_installations": "gate:status:read",
    "gate_alpha_get_webhook_status": "gate:status:read",
    "gate_alpha_control_installation": "gate:agents:manage",
    "gate_alpha_run_model_preflight": "gate:agents:manage",
    "gate_alpha_list_shadow_reviews": "gate:agents:manage",
    "gate_alpha_get_test_group_presence": "gate:agents:manage",
    "gate_alpha_send_review": "gate:messages:send",
    "gate_alpha_verify_review_replay": "gate:status:read",
    "gate_beta_export_space_checkpoint": "gate:agents:manage",
    "gate_beta_get_provider_probe": "gate:agents:manage",
}


# 24 MiB of image bytes expand to 32 MiB as base64. The remaining space
# covers captions, item metadata and the JSON-RPC envelope.
_MAX_MCP_BODY_BYTES = 34 * 1024 * 1024


async def _read_mcp_request(request: Request) -> JsonObject:
    return await read_mcp_payload(request, limit=_MAX_MCP_BODY_BYTES)


@router.post("/mcp")
async def mcp_endpoint(
    payload: Annotated[JsonObject, Depends(_read_mcp_request)],
    request: Request = None,
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
                    _gate_owner_reply_readiness_tool(settings),
                    _gate_save_reply_draft_tool(settings),
                    _gate_prepare_reply_document_tool(settings),
                    _gate_prepare_reply_media_tool(settings),
                    _gate_preview_reply_media_tool(settings),
                    _gate_send_approved_reply_tool(settings),
                    _gate_send_approved_document_tool(settings),
                    _gate_queue_approved_reply_sequence_tool(settings),
                    _gate_alpha_list_installations_tool(settings),
                    _gate_alpha_get_webhook_status_tool(settings),
                    _gate_alpha_control_installation_tool(settings),
                    _gate_alpha_run_model_preflight_tool(settings),
                    _gate_alpha_list_shadow_reviews_tool(settings),
                    _gate_alpha_get_test_group_presence_tool(settings),
                    _gate_alpha_send_review_tool(settings),
                    _gate_alpha_verify_review_replay_tool(settings),
                    _gate_beta_export_space_checkpoint_tool(settings),
                    _gate_beta_get_provider_probe_tool(settings),
                ] + personal_command_tools(settings)
            },
        )
    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict):
            return _jsonrpc_error(request_id=request_id, code=-32602, message="Invalid params")
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if isinstance(tool_name, str) and tool_name in PERSONAL_COMMAND_SCOPES:
            return await personal_command_call(
                request=request,
                request_id=request_id,
                tool_name=tool_name,
                arguments=arguments,
                authorization=authorization,
                settings=settings,
            )
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
            snapshot, calls_reasons = await build_status_snapshot(
                session=session,
                settings=settings,
                principal=principal,
                protocol_version=MCP_PROTOCOL_VERSION,
                tool_names=MCP_LEGACY_STATUS_TOOL_NAMES,
            )
            return _tool_result(snapshot, format_status_text(snapshot, calls_reasons), request_id)
        if tool_name == "gate_alpha_list_installations":
            return await _alpha_list_installations_tool_result(
                request_id=request_id,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_alpha_get_webhook_status":
            return await _alpha_get_webhook_status_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
            )
        if tool_name == "gate_alpha_list_shadow_reviews":
            return await _alpha_list_shadow_reviews_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_alpha_get_test_group_presence":
            return await _alpha_get_test_group_presence_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
            )
        if tool_name == "gate_alpha_send_review":
            return await _alpha_send_review_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
                request=request,
            )
        if tool_name == "gate_alpha_verify_review_replay":
            return await _alpha_verify_review_replay_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_beta_export_space_checkpoint":
            return await _beta_export_space_checkpoint_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_beta_get_provider_probe":
            return await _beta_get_provider_probe_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
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
        if tool_name == "gate_get_owner_reply_readiness":
            return await _owner_reply_readiness_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                request=request,
            )
        if tool_name == "gate_get_reply_context":
            return await _get_reply_context_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
            )
        if tool_name == "gate_save_reply_draft":
            return await _save_reply_draft_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
            )
        if tool_name == "gate_prepare_reply_media":
            return await media_tool_result(
                request_id=request_id, arguments=arguments, principal=principal,
                session=session, prepare=True,
            )
        if tool_name == "gate_preview_reply_media":
            return await media_tool_result(
                request_id=request_id, arguments=arguments, principal=principal,
                session=session, prepare=False,
            )
        if tool_name == "gate_prepare_reply_document":
            return await _prepare_reply_document_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                settings=settings,
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
        if tool_name == "gate_send_approved_document":
            return await _send_approved_document_tool_result(
                request_id=request_id,
                arguments=arguments,
                principal=principal,
                session=session,
                telegram=telegram,
                settings=settings,
            )
        if tool_name == "gate_queue_approved_reply_sequence":
            return await _queue_approved_reply_sequence_tool_result(
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


async def _alpha_send_review_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
    request: Request | None = None,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    raw_turn = arguments.get("turn_ref")
    raw_digest = arguments.get("expected_candidate_sha256")
    if (
        not isinstance(raw_ref, str)
        or not isinstance(raw_turn, str)
        or not isinstance(raw_digest, str)
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "installation_ref, turn_ref, expected_candidate_sha256 "
                "and confirmed=true are required"
            ),
        )
    if isinstance(raw_ref, str) and raw_ref.startswith("aqub-"):
        return _tool_error_result(
            request_id=request_id,
            code="aqub_project_frozen",
            message="AQUB is frozen and disconnected from Gate",
        )

    try:
        async with httpx.AsyncClient() as http_client:
            payload = await send_owner_test_review(
                session=session,
                settings=settings,
                http_client=http_client,
                workspace_key=principal.access_scope.workspace_id,
                installation_ref=raw_ref,
                turn_ref=raw_turn,
                expected_candidate_sha256=raw_digest,
                confirmed=True,
            )
    except (AlphaOwnerTestRuntimeError, ValueError) as exc:
        await session.rollback()
        code = exc.code if isinstance(exc, AlphaOwnerTestRuntimeError) else str(exc)
        return _tool_error_result(
            request_id=request_id,
            code=code,
            message="Alpha owner-test delivery failed closed",
        )
    return _tool_result(
        payload,
        (
            f"Alpha review delivery: {payload['state']}; "
            f"replay={payload['replay']}"
        ),
        request_id,
    )


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


def _gate_beta_export_space_checkpoint_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_beta_export_space_checkpoint",
        "title": "Export encrypted Beta Space checkpoint",
        "description": (
            "After exact owner confirmation, read one active Beta Space context "
            "stream and return only its X25519/AES-256-GCM encrypted portable "
            "checkpoint. It exposes no plaintext, Telegram IDs or identities "
            "and does not advance the export cursor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": ["installation_ref", "confirmed"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_beta_export_space_checkpoint"
        ],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:agents:manage",
    )


def _gate_beta_get_provider_probe_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_beta_get_provider_probe",
        "title": "Probe the Beta provider",
        "description": (
            "After exact owner confirmation, read the configured Beta bot and "
            "webhook state. New Wedding binding creation stays disabled until "
            "immutable expected chat and owner identifiers exist; this tool returns "
            "no Telegram identifiers or payloads, records no consent, and sends "
            "no message."
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
            "gate_beta_get_provider_probe"
        ],
        "annotations": {
            "readOnlyHint": True,
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


def _gate_alpha_get_webhook_status_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_get_webhook_status",
        "title": "Check Alpha webhook status",
        "description": (
            "After exact owner confirmation, call Telegram Bot API "
            "getWebhookInfo once for the configured Alpha bot and return only "
            "redacted webhook status. It cannot send messages, read history or "
            "participants, or change the webhook or installation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": ["installation_ref", "confirmed"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_get_webhook_status"
        ],
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
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


def _gate_alpha_get_test_group_presence_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_get_test_group_presence",
        "title": "Check owner-only Alpha test group",
        "description": (
            "After owner confirmation, inspect only the aggregate member count for "
            "one approved Alpha test-group installation. It never exposes identities "
            "and pauses the installation unless the group remains owner-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": ["installation_ref", "confirmed"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_get_test_group_presence"
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


def _gate_alpha_send_review_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_send_review",
        "title": "Send one exact Alpha review",
        "description": (
            "Send one exact model proposal only after rechecking that the approved "
            "test group still contains only the owner and bot. The same confirmed "
            "surface also admits the single bounded, idempotent AQUB General Cube "
            "release replay selector."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "turn_ref": {"type": "string", "format": "uuid"},
                "expected_candidate_sha256": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
                "confirmed": {"type": "boolean", "const": True},
            },
            "required": [
                "installation_ref",
                "turn_ref",
                "expected_candidate_sha256",
                "confirmed",
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_alpha_send_review"],
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


def _gate_alpha_verify_review_replay_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_alpha_verify_review_replay",
        "title": "Verify one stored Alpha replay",
        "description": (
            "Read one exact already-persisted terminal Alpha delivery receipt. "
            "It cannot call Telegram or any provider, create delivery records, "
            "or mutate installation state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "installation_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "turn_ref": {"type": "string", "format": "uuid"},
                "expected_candidate_sha256": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
            },
            "required": [
                "installation_ref",
                "turn_ref",
                "expected_candidate_sha256",
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_alpha_verify_review_replay"
        ],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(
        tool=tool,
        settings=settings,
        scope="gate:status:read",
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
            "Read reply_sequences for scoped series state, stop reason, sent/remaining items "
            "and ambiguous attempts. Stopped tails never resume automatically. "
            "Read context_ready and context_reason before composing. Historical cursor or "
            "dialog_events_cursor pages never grant a sendable snapshot; raw text continuation "
            "uses each event text_next_cursor. After projection catches up, perform a new "
            "no-cursor read and review all returned events. "
            "Return one scoped RU/BY conversation as a chronological text, voice "
            "and attachment timeline. Attachment metadata is listed without a "
            "provider call. Immediate same-chat reply snapshots and quoted excerpts "
            "identify the original text or document, even outside the history page. "
            "Unavailable reply sources are explicit. Attachment metadata needs no "
            "provider call; an exact task:attachment:message:index selector retrieves "
            "one file on explicit request. Existing exact voice and latest call/voice "
            "selectors remain available for compatibility."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "oneOf": [
                        {"type": "string", "format": "uuid"},
                        {
                            "type": "string",
                            "pattern": "^watch-voice(-file)?:(latest|[0-9a-fA-F-]{36})$",
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^[0-9a-fA-F-]{36}:voice:[0-9a-fA-F-]{36}$"
                            ),
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^[0-9a-fA-F-]{36}:attachment:"
                                "[0-9a-fA-F-]{36}:[0-9]+$"
                            ),
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^voice:latest:[a-z0-9_-]{1,64}$"
                            ),
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^voice:latest-file:[a-z0-9_-]{1,64}$"
                            ),
                        },
                        {
                            "type": "string",
                            "pattern": (
                                "^voice:latest-transcript:[a-z0-9_-]{1,64}$"
                            ),
                        },
                        {
                            "type": "string",
                            "enum": [
                                "call:latest",
                                "call:latest-file",
                                "call:latest-transcript",
                            ],
                        },
                    ],
                    "examples": [
                        "48aa85f2-651e-43c0-b027-bf9efb22084e",
                        "voice:latest-transcript:ru",
                        "call:latest-transcript",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
                "cursor": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"],
        "annotations": READ_ONLY_TOOL_ANNOTATIONS,
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:inbox:read")


def _gate_owner_reply_readiness_tool(settings: Settings) -> JsonObject:
    return _with_security_scheme(
        tool={
            "name": OWNER_REPLY_READINESS_TOOL_NAME,
            "title": "Read exact owner reply readiness",
            "description": (
                "Read metadata only for one own RU/BY direct dialog. The recipient must be "
                "the different explicitly granted owner account. Resolves a task UUID and "
                "sequence-worker health without message text, history, provider calls or writes. "
                "An optional exact provider_message_id correlates one fresh inbound task. "
                "The time-stamped unbound observation is not an AI-zero guarantee or permission "
                "to send; confirm the exact owner-only test scope before any live effect."
            ),
            "inputSchema": OWNER_REPLY_READINESS_INPUT_SCHEMA,
            "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[OWNER_REPLY_READINESS_TOOL_NAME],
            "annotations": READ_ONLY_TOOL_ANNOTATIONS,
        },
        settings=settings,
        scope=OWNER_REPLY_READINESS_SCOPE,
    )


async def _owner_reply_readiness_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    request: Request | None,
) -> JsonObject:
    alias = arguments.get("account_alias")
    recipient = arguments.get("recipient_chat_id")
    message_id = arguments.get("provider_message_id")
    if (
        set(arguments) - {"account_alias", "recipient_chat_id", "provider_message_id"}
        or not isinstance(alias, str)
        or alias not in ("RU", "BY")
        or not isinstance(recipient, str)
        or ("provider_message_id" in arguments and not isinstance(message_id, str))
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Exact owner account and recipient metadata are required",
        )
    try:
        content = await build_owner_reply_readiness(
            session=session,
            principal=principal,
            account_alias=alias,
            recipient_chat_id=recipient,
            provider_message_id=message_id,
            worker_registry=_get_worker_health_registry(request) if request is not None else None,
        )
    except OwnerReplyReadinessError as exc:
        return _tool_error_result(request_id=request_id, code=exc.code, message=exc.message)
    return _tool_result(
        content,
        "Exact owner reply metadata observed. No content read, provider call or mutation performed.",
        request_id,
    )


def _gate_save_reply_draft_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_save_reply_draft",
        "title": "Save a Gate reply draft",
        "description": (
            "Read gate_get_reply_context first and pass its last message ID (context_snapshot) as "
            "expected_context_snapshot. For context_changed, show the returned stop message "
            "in the same conversation and wait for Mark before preparing another reply. "
            "Save proposed text with text_format=plain, markdown, or rich. Rich requires rich_content (gate.rich.v1) and omitted or empty text; supports heading, paragraph, list, blockquote, pre, table. Rich is single-message only; no series/captions. "
            "Omitting text_format keeps the legacy literal contract without a formatting preview. "
            "Markdown supports bold, italic, strike, underline, spoiler, links, code, headings, lists and quotes; unsupported or malformed syntax is rejected. "
            "Returns exact normalized text, UTF-16 entities and versioned formatting preview/hash. "
            "Show that result before confirmation and pass its payload_sha256 to send/queue. "
            "A reply must fit 4096 UTF-16 units after conversion; no automatic splitting. "
            "For a task_id returned by answered_items, set follow_up=true explicitly. "
            "This tool never approves or sends the message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "expected_context_snapshot": {"type": "string", "pattern": SNAPSHOT_PATTERN},
                "text": {"type": "string", "maxLength": 65536},
                "text_format": {"type": "string", "enum": ["plain", "markdown", "rich"]},
                "rich_content": rich_content_schema(),
                "follow_up": {"type": "boolean", "default": False},
            },
            "required": ["task_id", "expected_context_snapshot"],
            "oneOf": [
                {
                    "properties": {"text_format": {"const": "rich"}, "text": {"const": ""}},
                    "required": ["text_format", "rich_content"],
                },
                {
                    "required": ["text"],
                    "not": {
                        "anyOf": [
                            {"required": ["rich_content"]},
                            {
                                "properties": {"text_format": {"const": "rich"}},
                                "required": ["text_format"],
                            },
                        ]
                    },
                },
            ],
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
            "For images use expected_media_package_sha256 from the complete byte-backed image preview. "
            "execution_id charges the same media execution budget for text regression. "
            "Use expected_payload_sha256 from the displayed formatting preview; "
            "required for explicit-mode drafts/captions. Legacy rows without a preview retain the old contract. "
            "Send the exact saved draft through its originating RU/BY Telegram "
            "Business account. Call only after showing the account, recipient and "
            "final text to Mark and receiving explicit confirmation. "
            "Report the outcome in the same conversation where the send was requested. "
            "For context_changed, show the returned stop message and wait for Mark "
            "before preparing another reply. Report a send only when sent=true; "
            "an unknown outcome must stay unconfirmed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "format": "uuid"},
                "confirmed": {"type": "boolean", "const": True},
                "expected_payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "expected_media_package_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "execution_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"},
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


def _gate_prepare_reply_document_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_prepare_reply_document",
        "title": "Prepare one Gate Business document",
        "description": (
            "Read gate_get_reply_context first and pass its last message ID (context_snapshot) as "
            "expected_context_snapshot. A changed dialog requires a new draft and approval. "
            "Stage one original PDF/ZIP, Excel/Word/PowerPoint or JPEG/PNG/WEBP/GIF file as a document; "
            "No conversion/compression. Max 8 MiB; private staging 24 hours. "
            "Use caption_format=plain or markdown. Omitting it preserves legacy literal captions. "
            "Returns saved caption text/entities/version/hash; max 1024 UTF-16 units after conversion. "
            "Pass its formatting.payload_sha256 to approved send after showing the preview. "
            "The base64 bytes are accepted only in this upload argument, never "
            "in a text draft or tool response. No message is sent. Show Mark "
            "account, recipient, file name, MIME, size, SHA-256 and exact caption."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
                "expected_context_snapshot": {"type": "string", "pattern": SNAPSHOT_PATTERN},
                "filename": {"type": "string", "minLength": 1, "maxLength": 255},
                "media_type": {"type": "string", "enum": DOCUMENT_MIME_TYPES},
                "content_base64": {"type": "string", "minLength": 1, "maxLength": 11184812},
                "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "caption": {"type": "string", "maxLength": 65536, "default": ""},
                "caption_format": {"type": "string", "enum": ["plain", "markdown"]},
                "follow_up": {"type": "boolean", "default": False},
            },
            "required": [
                "task_id", "filename", "media_type", "content_base64", "expected_sha256",
                "expected_context_snapshot"
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_prepare_reply_document"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:drafts:write")


def _gate_send_approved_document_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_send_approved_document",
        "title": "Send one explicitly approved Gate Business document",
        "description": (
            "Use expected_payload_sha256 from the displayed formatting preview; "
            "required for explicit-mode drafts/captions. Legacy rows without a preview retain the old contract. "
            "Send the exact staged file through its originating RU/BY Business "
            "account. Call only after Mark has confirmed the displayed account, "
            "recipient, filename, size, SHA-256 and exact caption."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "format": "uuid"},
                "confirmed": {"type": "boolean", "const": True},
                "expected_payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "expected_account": {"type": "string", "enum": ["RU", "BY"]},
                "expected_recipient_chat_id": {"type": "string", "minLength": 1},
                "expected_document_filename": {"type": "string", "minLength": 1},
                "expected_document_size_bytes": {"type": "integer", "minimum": 1},
                "expected_document_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "expected_document_caption": {"type": "string", "maxLength": 1024},
            },
            "required": [
                "draft_id", "confirmed", "expected_account", "expected_recipient_chat_id",
                "expected_document_filename", "expected_document_size_bytes",
                "expected_document_sha256", "expected_document_caption",
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS["gate_send_approved_document"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }
    return _with_security_scheme(tool=tool, settings=settings, scope="gate:messages:send")


def _gate_queue_approved_reply_sequence_tool(settings: Settings) -> JsonObject:
    tool = {
        "name": "gate_queue_approved_reply_sequence",
        "title": "Queue an explicitly approved Gate reply sequence",
        "description": (
            "Include expected_payload_sha256s in draft order from each displayed formatting preview; "
            "required for explicit-mode drafts. A legacy-only sequence may omit digests. "
            "Drafts must retain the context_snapshot reviewed at preparation. "
            "A freshness refusal returns sent=false and scoped current_context when "
            "gate:inbox:read is granted; review changes, prepare new drafts and obtain "
            "a new exact approval. Never refresh stale drafts automatically. "
            "Never split or reformat during delivery. "
            f"Queue {MIN_SEQUENCE_DRAFTS} to {MAX_SEQUENCE_DRAFTS} saved drafts "
            "for the same RU/BY recipient. "
            "Call only after showing Mark the account, recipient and every final "
            "message in order and receiving one explicit confirmation of the whole "
            "sequence. The first item becomes due immediately and later items no "
            "sooner than 60 seconds after the preceding confirmed delivery. "
            "A new foreign dialog event durably stops the unsent tail. Read reply_sequences "
            "in scoped reply context; no automatic resume or implicit approval of remaining drafts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_ids": {
                    "type": "array",
                    "minItems": MIN_SEQUENCE_DRAFTS,
                    "maxItems": MAX_SEQUENCE_DRAFTS,
                    "uniqueItems": True,
                    "items": {"type": "string", "format": "uuid"},
                },
                "confirmed": {"type": "boolean", "const": True},
                "expected_payload_sha256s": {"type": "array", "minItems": MIN_SEQUENCE_DRAFTS, "maxItems": MAX_SEQUENCE_DRAFTS, "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                "expected_account": {
                    "type": "string",
                    "enum": ["RU", "BY"],
                },
                "expected_recipient_chat_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                },
                "interval_seconds": {"type": "integer", "const": 60},
            },
            "required": [
                "draft_ids",
                "confirmed",
                "expected_account",
                "expected_recipient_chat_id",
                "interval_seconds",
            ],
            "additionalProperties": False,
        },
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[
            "gate_queue_approved_reply_sequence"
        ],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
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
    if arguments.keys() - {
        "expected_context_snapshot",
        "task_id",
        "text",
        "text_format",
        "rich_content",
        "follow_up",
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    raw_task_id = arguments.get("task_id")
    raw_text = arguments.get("text", "" if arguments.get("text_format") == "rich" else None)
    raw_follow_up = arguments.get("follow_up", False)
    text_format = arguments.get("text_format")
    if (
        not isinstance(raw_task_id, str)
        or not isinstance(raw_text, str)
        or not isinstance(raw_follow_up, bool)
        or ("rich_content" in arguments and text_format != "rich")
        or ("text_format" in arguments and text_format not in ("plain", "markdown", "rich"))
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id and text are required; follow_up must be boolean",
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
            SaveReplyDraftCommand(
                principal=principal,
                task_id=task_id,
                expected_context_snapshot=arguments.get("expected_context_snapshot"),
                text=raw_text,
                follow_up=raw_follow_up,
                text_format=text_format,
                rich_content=arguments.get("rich_content"),
            )
        )
    except ReplyDraftError as exc:
        if exc.code in {"context_changed", "context_snapshot_required", "context_unavailable"}:
            return await _dialog_context_error_result(
                request_id=request_id, code=exc.code, message=exc.message,
                principal=principal, session=session, task_id=task_id,
            )
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
        **({"formatting": saved.formatting.to_dict()} if saved.formatting else {}),
        "sent": False,
        "approval_required": True,
        "send_available": True,
        "confirmation": {
            **({"expected_payload_sha256": saved.formatting.payload_sha256}
               if saved.formatting else {}),
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
            + ("Not sent. Show exact text and formatting preview; confirm its hash, account and recipient."
               if saved.formatting else
               "Not sent. Show exact text, account and recipient before confirmation.")
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
    if arguments.keys() - {
        'draft_id',
        'confirmed',
        'expected_account',
        'expected_recipient_chat_id',
        'expected_payload_sha256',
        'expected_media_package_sha256',
        'execution_id',
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    expected_payload_sha256 = arguments.get("expected_payload_sha256")
    if "expected_payload_sha256" in arguments and not isinstance(expected_payload_sha256, str):
        return _tool_error_result(request_id=request_id, code="invalid_arguments", message="expected_payload_sha256 must be a SHA-256 string")
    expected_media_package_sha256 = arguments.get("expected_media_package_sha256")
    execution_id = arguments.get("execution_id")
    for key in ("expected_media_package_sha256", "execution_id"):
        if key in arguments and not isinstance(arguments[key], str):
            return _tool_error_result(request_id=request_id, code="invalid_arguments", message=f"{key} must be a string")
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
                expected_payload_sha256=expected_payload_sha256,
                expected_media_package_sha256=expected_media_package_sha256,
                execution_id=execution_id,
                confirmed=confirmed,
                expected_account=expected_account,
                expected_recipient_chat_id=expected_recipient,
            )
        )
    except ReplySendError as exc:
        if exc.code in {"context_changed", "context_snapshot_required", "context_unavailable"}:
            return await _dialog_context_error_result(
                request_id=request_id, code=exc.code, message=exc.message,
                principal=principal, session=session, draft_id=draft_id,
            )
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
        **({"delivery_mode": sent.delivery_mode, "draft_revision": sent.draft_revision,
            "runtime_revision": sent.runtime_revision, "reservation_time": sent.reservation_time}
           if sent.delivery_mode == "rich" else {}),
        **({"payload_sha256": sent.payload_sha256} if sent.payload_sha256 is not None else {}),
        "provider_message_id": sent.provider_message_id,
        "error_code": sent.error_code,
        "error_detail": sent.error_detail,
    }
    if sent.media is not None:
        content["media"] = sent.media
        content.update(delivery_mode=sent.delivery_mode, draft_revision=sent.draft_revision,
                       runtime_revision=sent.runtime_revision, reservation_time=sent.reservation_time)
    if sent.sent:
        text = (
            f"Reply sent through {sent.account_label} to chat "
            f"{sent.recipient_chat_id}. Attempt: {str(sent.attempt_id)[:8]}."
        )
    elif sent.status == SendAttemptStatus.FAILED.value and sent.error_code == "context_changed":
        text = CONTEXT_CHANGED_REPLY_TEXT
    else:
        text = (
            f"Reply was not confirmed as sent. Status: {sent.status}. "
            f"Account: {sent.account_label}. Attempt: {str(sent.attempt_id)[:8]}. "
            "Do not retry this draft automatically."
        )
    return _tool_result(content, text, request_id)


async def _prepare_reply_document_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if arguments.keys() - {
        "expected_context_snapshot",
        'task_id',
        'filename',
        'media_type',
        'content_base64',
        'expected_sha256',
        'caption',
        'caption_format',
        'follow_up',
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    if not settings.gate_document_send_enabled:
        return _tool_error_result(
            request_id=request_id,
            code="document_send_disabled",
            message="Business document preparation is disabled",
        )
    fields = (
        "task_id", "filename", "media_type", "content_base64", "expected_sha256"
    )
    if any(not isinstance(arguments.get(key), str) for key in fields):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Task and exact document metadata are required",
        )
    caption = arguments.get("caption", "")
    caption_format = arguments.get("caption_format")
    follow_up = arguments.get("follow_up", False)
    if caption_format == "rich":
        return _tool_error_result(
            request_id=request_id,
            code="rich_surface_unsupported",
            message="Rich document captions are unsupported in sprint 1",
        )
    if not isinstance(caption, str) or type(follow_up) is not bool or ("caption_format" in arguments and caption_format not in ("plain", "markdown")):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Caption and follow_up must have the correct types",
        )
    try:
        task_id = UUID(arguments["task_id"])
    except ValueError:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments", message="task_id must be UUID"
        )
    try:
        prepared = await PrepareReplyDocumentService(
            SqlReplyDocumentWriter(session)
        ).execute(
            PrepareReplyDocumentCommand(
                principal=principal,
                task_id=task_id,
                expected_context_snapshot=arguments.get("expected_context_snapshot"),
                filename=arguments["filename"],
                media_type=arguments["media_type"],
                content_base64=arguments["content_base64"],
                expected_sha256=arguments["expected_sha256"],
                caption=caption,
                caption_format=caption_format,
                follow_up=follow_up,
            )
        )
    except ReplyDraftError as exc:
        if exc.code in {"context_changed", "context_snapshot_required", "context_unavailable"}:
            return await _dialog_context_error_result(
                request_id=request_id, code=exc.code, message=exc.message,
                principal=principal, session=session, task_id=task_id,
            )
        return _tool_error_result(
            request_id=request_id, code=exc.code, message=exc.message
        )
    await session.commit()
    content = {
        "draft_id": str(prepared.draft_id),
        "task_id": str(prepared.task_id),
        "account": prepared.account_label,
        "recipient_chat_id": prepared.recipient_chat_id,
        "filename": prepared.filename,
        "media_type": prepared.media_type,
        "size_bytes": prepared.size_bytes,
        "sha256": prepared.sha256,
        "caption": prepared.caption,
        **({"formatting": prepared.formatting.to_dict()} if prepared.formatting else {}),
        "sent": False,
        "approval_required": True,
    }
    return _tool_result(
        content,
        (
            f"Document prepared; not sent. Account {prepared.account_label}; "
            f"recipient {prepared.recipient_chat_id}; file {prepared.filename}; "
            f"mode document (original file); MIME {prepared.media_type}; size {prepared.size_bytes}; "
            f"SHA-256 {prepared.sha256}; caption {prepared.caption!r}. "
            "Show these exact values to Mark for confirmation."
        ),
        request_id,
    )


async def _send_approved_document_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
) -> JsonObject:
    if arguments.keys() - {
        'draft_id',
        'confirmed',
        'expected_account',
        'expected_recipient_chat_id',
        'expected_document_filename',
        'expected_document_size_bytes',
        'expected_document_sha256',
        'expected_document_caption',
        'expected_payload_sha256',
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    expected_payload_sha256 = arguments.get("expected_payload_sha256")
    if "expected_payload_sha256" in arguments and not isinstance(expected_payload_sha256, str):
        return _tool_error_result(request_id=request_id, code="invalid_arguments", message="expected_payload_sha256 must be a SHA-256 string")
    names = (
        "draft_id", "expected_account", "expected_recipient_chat_id",
        "expected_document_filename", "expected_document_sha256",
        "expected_document_caption",
    )
    if (
        any(not isinstance(arguments.get(key), str) for key in names)
        or type(arguments.get("expected_document_size_bytes")) is not int
        or type(arguments.get("confirmed")) is not bool
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Exact document confirmation is required",
        )
    try:
        draft_id = UUID(arguments["draft_id"])
    except ValueError:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments", message="draft_id must be UUID"
        )
    try:
        sent = await SendApprovedReplyService(
            SqlTelegramReplySender(session=session, telegram=telegram, settings=settings)
        ).execute(
            SendApprovedReplyCommand(
                principal=principal,
                draft_id=draft_id,
                expected_payload_sha256=expected_payload_sha256,
                confirmed=arguments["confirmed"],
                expected_account=arguments["expected_account"],
                expected_recipient_chat_id=arguments["expected_recipient_chat_id"],
                expected_document_filename=arguments["expected_document_filename"],
                expected_document_size_bytes=arguments["expected_document_size_bytes"],
                expected_document_sha256=arguments["expected_document_sha256"],
                expected_document_caption=arguments["expected_document_caption"],
            )
        )
    except ReplySendError as exc:
        if exc.code in {"context_changed", "context_snapshot_required", "context_unavailable"}:
            return await _dialog_context_error_result(
                request_id=request_id, code=exc.code, message=exc.message,
                principal=principal, session=session, draft_id=draft_id,
            )
        return _tool_error_result(
            request_id=request_id, code=exc.code, message=exc.message
        )
    content = {
        "attempt_id": str(sent.attempt_id),
        "draft_id": str(sent.draft_id),
        "task_id": str(sent.task_id),
        "account": sent.account_label,
        "recipient_chat_id": sent.recipient_chat_id,
        "filename": sent.document_filename,
        "media_type": sent.document_media_type,
        "size_bytes": sent.document_size_bytes,
        "sha256": sent.document_sha256,
        "status": sent.status,
        "sent": sent.sent,
        "idempotent_replay": sent.idempotent_replay,
        **({"payload_sha256": sent.payload_sha256} if sent.payload_sha256 is not None else {}),
        "provider_message_id": sent.provider_message_id,
        "error_code": sent.error_code,
        "error_detail": sent.error_detail,
    }
    return _tool_result(
        content,
        (
            f"Document status {sent.status}; attempt {str(sent.attempt_id)[:8]}. "
            + (
                "Match the provider message id against scoped reply context."
                if sent.sent
                else "Do not automatically retry this prepared document."
            )
        ),
        request_id,
    )


async def _queue_approved_reply_sequence_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    if arguments.keys() - {
        'draft_ids',
        'confirmed',
        'expected_account',
        'expected_recipient_chat_id',
        'interval_seconds',
        'expected_payload_sha256s',
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    raw_hashes = arguments.get("expected_payload_sha256s")
    if "expected_payload_sha256s" in arguments and (not isinstance(raw_hashes, list) or not all(isinstance(item, str) for item in raw_hashes)):
        return _tool_error_result(request_id=request_id, code="invalid_arguments", message="expected_payload_sha256s must contain ordered SHA-256 strings")
    raw_draft_ids = arguments.get("draft_ids")
    confirmed = arguments.get("confirmed")
    expected_account = arguments.get("expected_account")
    expected_recipient = arguments.get("expected_recipient_chat_id")
    interval_seconds = arguments.get("interval_seconds")
    if (
        not isinstance(raw_draft_ids, list)
        or not all(isinstance(item, str) for item in raw_draft_ids)
        or not isinstance(confirmed, bool)
        or not isinstance(expected_account, str)
        or not isinstance(expected_recipient, str)
        or not isinstance(interval_seconds, int)
        or isinstance(interval_seconds, bool)
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "draft_ids, confirmed, expected_account, "
                "expected_recipient_chat_id and interval_seconds are required"
            ),
        )
    try:
        draft_ids = tuple(UUID(item) for item in raw_draft_ids)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Every draft_id must be a UUID",
        )

    service = QueueApprovedReplySequenceService(SqlReplySequenceWriter(session))
    try:
        queued = await service.execute(
            QueueApprovedReplySequenceCommand(
                principal=principal,
                draft_ids=draft_ids,
                confirmed=confirmed,
                expected_account=expected_account,
                expected_recipient_chat_id=expected_recipient,
                interval_seconds=interval_seconds,
                expected_payload_sha256s=tuple(raw_hashes) if raw_hashes is not None else None,
            )
        )
    except ReplySequenceError as exc:
        if exc.code in {"context_changed", "context_snapshot_required", "context_unavailable"}:
            return await _dialog_context_error_result(
                request_id=request_id, code=exc.code, message=exc.message,
                principal=principal, session=session, draft_id=draft_ids[0],
            )
        await session.rollback()
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    await session.commit()
    content = {
        "sequence_id": str(queued.sequence_id),
        "sequence_ref": str(queued.sequence_id)[:8],
        "task_id": str(queued.task_id),
        "account_id": str(queued.account_id),
        "project_id": str(queued.project_id),
        "account": {
            "alias": queued.account_alias,
            "label": queued.account_label,
        },
        "recipient_chat_id": queued.recipient_chat_id,
        "interval_seconds": queued.interval_seconds,
        "queued": True,
        "sent": False,
        "items": [
            {
                "draft_id": str(item.draft_id),
                "position": item.position,
                **({"payload_sha256": item.payload_sha256}
                   if item.payload_sha256 is not None else {}),
                "scheduled_at": item.scheduled_at.isoformat(),
            }
            for item in queued.items
        ],
    }
    return _tool_result(
        content,
        (
            f"Reply sequence queued through {queued.account_label} for chat "
            f"{queued.recipient_chat_id}: {len(queued.items)} messages, "
            f"{queued.interval_seconds}-second minimum interval. "
            "No delivery was attempted by this tool call."
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


async def _dialog_context_error_result(
    *, request_id: Any, code: str, message: str, principal: Principal,
    session: AsyncSession, task_id: UUID | None = None, draft_id: UUID | None = None,
) -> JsonObject:
    await session.rollback()
    current_context = None
    details: JsonObject = {"sent": False, "current_context": None}
    if code == "context_changed":
        message = CONTEXT_CHANGED_REPLY_TEXT
    elif code == "context_unavailable":
        details["reason"] = message
    try:
        if "gate:inbox:read" not in principal.scopes:
            return _tool_error_result(
                request_id=request_id, code=code, message=message,
                details=details,
            )
        if task_id is None and draft_id is not None:
            scope = principal.access_scope
            task_id = await session.scalar(select(ReplyDraft.reply_task_id).where(
                ReplyDraft.id == draft_id,
                ReplyDraft.account_id.in_(scope.account_ids),
                ReplyDraft.project_id.in_(scope.project_ids),
            ))
        if task_id is not None:
            current_context = await get_reply_context(
                session=session, scope=principal.access_scope, task_id=task_id, limit=30,
            )
    except Exception:
        # A failed context read cannot turn a refusal into a send or leak DB details.
        await session.rollback()
    details["current_context"] = current_context
    return _tool_error_result(
        request_id=request_id, code=code, message=message,
        details=details,
    )


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


def _commit(settings: Settings) -> str:
    return settings.deployment_sha[:12] if settings.deployment_sha != "unknown" else "unknown"


def _gate_media_tool(settings: Settings, *, prepare: bool) -> JsonObject:
    name = "gate_prepare_reply_media" if prepare else "gate_preview_reply_media"
    scope = "gate:drafts:write" if prepare else "gate:inbox:read"
    return _with_security_scheme(tool={
        "name": name,
        "title": "Upload and prepare Gate images" if prepare else "Preview prepared Gate images",
        "description": (
            "Upload exact PNG/JPEG bytes as structured base64; no URLs or server paths. "
            "Prepare photo, album or native Rich photo/slideshow/collage. Rich input uses "
            "gate.rich.v2 and batch aliases item:1 through item:N; staging replaces them "
            "with immutable scoped refs and output hashes. execution_expires_at is the exact "
            "bounded authorization deadline (at most 48h); shared budget is 12 writes/20 messages. "
            "Returns actual prepared images plus full snapshot/hash, captions and order. Not sent. "
            if prepare else "Read the actual immutable prepared image bytes and full snapshot "
            "inside the principal scope. Show all images, transforms, captions, order, sender, "
            "recipient and package hash before approved send."
        ),
        "inputSchema": PREPARE_INPUT_SCHEMA if prepare else PREVIEW_INPUT_SCHEMA,
        "outputSchema": MCP_TOOL_OUTPUT_SCHEMAS[name],
        "annotations": {"readOnlyHint": not prepare, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    }, settings=settings, scope=scope)


def _gate_prepare_reply_media_tool(settings: Settings) -> JsonObject:
    return _gate_media_tool(settings, prepare=True)


def _gate_preview_reply_media_tool(settings: Settings) -> JsonObject:
    return _gate_media_tool(settings, prepare=False)
