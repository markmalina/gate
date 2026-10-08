# mypy: ignore-errors
# ruff: noqa
"""Scoped Gate MCP endpoint for inbox, drafts and explicit reply sending."""

from typing import Annotated, Any

import base64
import hashlib
import mimetypes
import httpx
import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.owner_voice_recovery import owner_voice_archive_inventory
from app.infrastructure.watch_voice_recovery import protected_recovery_receipt
from app.infrastructure.watch_voice_post_payment import protected_post_payment_receipt
from app.api.deps import get_db_session
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.api.health_routes import _get_worker_health_registry
from app.api.routes import get_telegram_client
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import AlphaLifecycleError, AlphaLifecycleService
from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    alpha_model_readiness_payload,
    alpha_release_binding_readiness,
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
from app.application.aqub_transport import AQUB_TELEGRAM_WEBHOOK_PATH
from app.application.mcp_inbox import (
    build_unanswered_list,
    counterparty as _counterparty,
    get_reply_context,
)
from app.application.mcp_status import (
    build_gate_status,
    serialize_connection_health as _serialize_connection_health,
)
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
from app.models.watch_voice import WatchVoiceNote
from app.infrastructure.watch_voice_actions import operational_status as watch_voice_actions_status
from app.worker.watch_voice import operational_status as watch_voice_operational_status
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
from app.infrastructure.alpha_owner_test_runtime import (
    AlphaOwnerTestRuntimeError,
    observe_owner_test_presence,
    send_owner_test_review,
    verify_owner_test_review_replay,
)
from app.infrastructure.alpha_shadow_reviews import list_alpha_shadow_reviews
from app.infrastructure.aqub_general_release_replay import (
    AQUB_GENERAL_CUBE0_RECOVERY_INSTALLATION_REF,
    AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
    AQUBGeneralReleaseReplayError,
    recover_aqub_general_cube0,
    send_aqub_general_release_replay,
    verify_aqub_general_release_replay,
)
from app.infrastructure.become_controlled_intake import (
    become_controlled_intake_status,
)
from app.infrastructure.become_i4_connectivity import (
    probe_all_i4_connectivity,
)
from app.infrastructure.become_i4_recovery import (
    become_i4_recovery_status,
)
from app.infrastructure.become_quest import become_quest_status
from app.infrastructure.become_slice3_status import become_slice3_production_evidence
from app.infrastructure.beta_checkpoint_export import (
    BetaCheckpointExportError,
    export_beta_space_checkpoint,
)
from app.infrastructure.beta_space import beta_katya_space_operational_status
from app.infrastructure.beta_wedding_binding import (
    beta_wedding_space_operational_status,
)
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.dropbox_call_status import dropbox_call_operational_status
from app.infrastructure.historical_debt_diagnostics import historical_debt_diagnostics
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)
from app.infrastructure.telegram_voice_status import telegram_voice_operational_status
from app.infrastructure.telegram_webhook_status import (
    TelegramWebhookStatusError,
    get_alpha_webhook_status,
    get_beta_provider_probe,
)
from app.infrastructure.universal_mayak_status import (
    universal_mayak_operational_status,
)
from app.infrastructure.telegram_business_text_parity import (
    telegram_business_text_parity_status,
)
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_documents import SqlReplyDocumentWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.infrastructure.alpha_canary import (
    alpha_canary_active_installation_count,
    alpha_canary_audit_counts,
    alpha_canary_binding_count,
    alpha_canary_installation_count,
)
from app.models.domain import (
    UNCLASSIFIED_PROJECT_ID,
    Account,
    GateEvent,
    Message,
    MessageDirection,
    PhoneCallRecording,
    Project,
    ReplyDraft,
    ReplyTask,
    TelegramReceipt,
    TelegramUpdate,
    TelegramVoiceArchive,
)
from app.models.canonical import CanonicalMessage
from app.services.telegram import TelegramClient, build_telegram_http_client
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceError,
    TelegramVoiceOutcome,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
)
from app.application.telegram_attachments import (
    TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES,
    TelegramAttachmentError,
    TelegramAttachmentReference,
    extract_telegram_attachments,
    validated_telegram_receipt_payload,
)
from app.application.telegram_voice_update import extract_telegram_voice

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
    OWNER_REPLY_READINESS_TOOL_NAME,
    "gate_save_reply_draft",
    "gate_prepare_reply_document",
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
    name for name in MCP_TOOL_NAMES if name != OWNER_REPLY_READINESS_TOOL_NAME
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


@router.post("/mcp")
async def mcp_endpoint(
    payload: JsonObject,
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
                tool_names=MCP_LEGACY_STATUS_TOOL_NAMES,
            )
            snapshot["dropbox_calls"] = await dropbox_call_operational_status(
                session,
                workspace_key=principal.access_scope.workspace_id,
                configured_workspace=settings.gate_workspace_id,
                account_id=settings.dropbox_call_account_id,
                project_ids=frozenset(str(value) for value in principal.access_scope.project_ids),
                intake_project_allowed=UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids,
                enabled=settings.gate_dropbox_call_intake_enabled,
                configuration={
                    "app_key": bool(settings.dropbox_call_app_key),
                    "app_secret": bool(settings.dropbox_call_app_secret.get_secret_value()),
                    "refresh_token": bool(settings.dropbox_call_refresh_token.get_secret_value()),
                    "account_id": bool(settings.dropbox_call_account_id),
                    "openai_key": bool(settings.openai_api_key.get_secret_value()),
                    "inbox_path_valid": settings.dropbox_call_inbox_path == "/Alpha/Calls",
                },
            )
            if settings.gate_watch_voice_actions_enabled:
                snapshot["watch_voice_actions"] = await watch_voice_actions_status(
                    session, settings,
                    scope_allowed=(principal.access_scope.workspace_id == settings.gate_workspace_id
                                   and UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids),
                )
            if settings.gate_watch_voice_enabled:
                snapshot["watch_voice"] = await watch_voice_operational_status(
                    session, settings,
                    scope_allowed=(principal.access_scope.workspace_id == settings.gate_workspace_id
                                   and UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids),
                )
            snapshot["alpha_canary"] = await _build_alpha_canary_status(
                session=session,
                settings=settings,
                workspace_key=principal.access_scope.workspace_id,
            )
            snapshot["beta_katya_space"] = await beta_katya_space_operational_status(
                session,
                workspace_key=principal.access_scope.workspace_id,
                settings=settings,
            )
            snapshot["beta_wedding_space"] = (
                await beta_wedding_space_operational_status(
                    session,
                    workspace_key=principal.access_scope.workspace_id,
                    settings=settings,
                )
            )
            snapshot["universal_mayak"] = await universal_mayak_operational_status(
                session,
                workspace_key=principal.access_scope.workspace_id,
                settings=settings,
            )
            become_status = await become_controlled_intake_status(
                session,
                workspace_key=principal.access_scope.workspace_id,
                transcription_enabled=(
                    settings.gate_become_voice_transcription_enabled
                ),
                transcription_data_gate_approved=(
                    settings.gate_become_voice_transcription_data_gate_approved
                ),
                transcription_max_calls=(
                    settings.gate_become_voice_transcription_max_calls
                ),
                transcription_api_key_configured=bool(
                    settings.openai_api_key.get_secret_value()
                ),
                chain_execution_enabled=(
                    settings.gate_become_i4_chain_execution_enabled
                ),
                now=datetime.now(UTC),
            )
            recovery_status = await become_i4_recovery_status(
                session,
                workspace_key=principal.access_scope.workspace_id,
                execution_enabled=(
                    settings.gate_become_i4_recovery_execution_enabled
                ),
                max_posts=settings.gate_become_i4_recovery_max_posts,
            )
            become_status.update(recovery_status)
            become_status.update(
                await become_quest_status(
                    session,
                    workspace_key=principal.access_scope.workspace_id,
                )
            )
            become_status["chain_reconciliation_required"] = bool(
                become_status["chain_reconciliation_required"]
                or recovery_status["chain_recovery_reconciliation_required"]
            )
            async with httpx.AsyncClient() as connectivity_client:
                become_status.update(
                    await probe_all_i4_connectivity(
                        connectivity_client,
                    )
                )
            snapshot["become_controlled_intake"] = become_status
            snapshot["telegram_voice"] = (
                await telegram_voice_operational_status(
                    session,
                    workspace_key=principal.access_scope.workspace_id,
                    enabled=settings.gate_telegram_voice_transcription_enabled,
                    api_key_configured=bool(
                        settings.openai_api_key.get_secret_value()
                    ),
                    model=settings.gate_telegram_voice_transcription_model,
                    now=datetime.now(UTC),
                )
            ).payload()
            snapshot["telegram_business_text_parity"] = (
                await telegram_business_text_parity_status(
                    session,
                    workspace_key=principal.access_scope.workspace_id,
                    account_ids=principal.access_scope.account_ids,
                )
            ).payload()
            snapshot["telegram_voice_archives"] = await owner_voice_archive_inventory(
                session,
                workspace_key=principal.access_scope.workspace_id,
                account_ids=principal.access_scope.account_ids,
                project_ids=principal.access_scope.project_ids,
            )
            snapshot["historical_debt"] = await historical_debt_diagnostics(
                session,
                workspace_key=principal.access_scope.workspace_id,
                account_ids=principal.access_scope.account_ids,
                project_ids=principal.access_scope.project_ids,
            )
            async with httpx.AsyncClient() as slice3_status_client:
                snapshot["become_slice3"] = await become_slice3_production_evidence(
                    session,
                    settings=settings,
                    http_client=slice3_status_client,
                    now=datetime.now(UTC),
                )
            return _tool_result(snapshot, _format_status_text(snapshot), request_id)
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


async def _get_reply_context_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_task_id = arguments.get("task_id")
    if not isinstance(raw_task_id, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id is required",
        )
    if raw_task_id.startswith("watch-voice:") or raw_task_id.startswith("watch-voice-file:"):
        return await _watch_voice_tool_result(
            request_id=request_id, selector=raw_task_id, principal=principal,
            session=session, settings=settings,
        )
    try:
        call_selector = _parse_latest_call_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest call selector is invalid",
        )
    if call_selector is not None:
        return await _latest_phone_call_tool_result(
            request_id=request_id,
            principal=principal,
            session=session,
            settings=settings,
            payload_kind=call_selector,
        )
    try:
        latest_voice_transcript_alias = _parse_latest_voice_transcript_selector(
            raw_task_id
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice transcript selector is invalid",
        )
    if latest_voice_transcript_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_transcript_alias,
            principal=principal,
            session=session,
            settings=settings,
            transcript_payload=True,
        )
    try:
        latest_voice_file_alias = _parse_latest_voice_file_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice file selector is invalid",
        )
    if latest_voice_file_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_file_alias,
            principal=principal,
            session=session,
            file_payload=True,
        )
    try:
        latest_voice_alias = _parse_latest_voice_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice selector is invalid",
        )
    if latest_voice_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_alias,
            principal=principal,
            session=session,
        )
    try:
        task_id, voice_message_id, attachment_selection = (
            _parse_reply_context_selector(raw_task_id)
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "task_id must be a UUID or an exact task:voice:message "
                "or task:attachment:message:index selector"
            ),
        )

    raw_cursor = arguments.get("cursor")
    if raw_cursor is not None and not isinstance(raw_cursor, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="cursor must be an opaque string",
        )
    try:
        context = await get_reply_context(
            session=session,
            scope=principal.access_scope,
            task_id=task_id,
            limit=_bounded_limit(arguments.get("limit"), default=20, maximum=100),
            cursor=raw_cursor,
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="reply context cursor is invalid for this task",
        )
    if context is None:
        return _tool_error_result(
            request_id=request_id,
            code="task_not_found",
            message="Reply task not found in the allowed scope",
        )
    if voice_message_id is not None:
        return await _voice_reply_context_tool_result(
            request_id=request_id,
            context=context,
            message_id=voice_message_id,
            principal=principal,
            session=session,
            settings=settings,
        )
    if attachment_selection is not None:
        attachment_message_id, attachment_index = attachment_selection
        return await _attachment_reply_context_tool_result(
            request_id=request_id,
            context=context,
            message_id=attachment_message_id,
            attachment_index=attachment_index,
            principal=principal,
            session=session,
            settings=settings,
        )
    return _tool_result(
        context,
        _format_reply_context_text(context),
        request_id,
    )


def _parse_latest_voice_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice selector")
    return alias


async def _watch_voice_tool_result(
    *,
    request_id: Any,
    selector: str,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if (
        principal.access_scope.workspace_id != settings.gate_workspace_id
        or UNCLASSIFIED_PROJECT_ID not in principal.access_scope.project_ids
    ):
        return _tool_error_result(
            request_id=request_id,
            code="watch_voice_scope_denied",
            message="Watch voice is outside the allowed owner scope",
        )
    prefix, ref = selector.split(":", 1)
    try:
        note_id = None if ref == "latest" else str(UUID(ref))
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Exact watch voice UUID or latest is required",
        )
    query = select(WatchVoiceNote).where(
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    )
    if note_id is not None:
        query = query.where(WatchVoiceNote.id == note_id)
    row = await session.scalar(query.order_by(WatchVoiceNote.received_at.desc()).limit(1))
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="watch_voice_archive_empty",
            message="No watch voice archive exists in this owner scope",
        )
    context = {
        "task_id": row.id,
        "task_ref": row.id[:8],
        "status": "watch_voice_" + row.transcription_state,
        "reply_required": False,
        "account": {"id": row.id, "alias": "watch_voice", "label": "WATCH VOICE"},
        "conversation": {"id": row.id, "chat_id": "watch-voice", "business_connection_id": None},
        "counterparty": {
            "platform": "watch_voice_dropbox",
            "platform_user_id": None,
            "display_name": None,
            "username": None,
            "label": None,
        },
        "messages": [],
        "count": 0,
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
    }
    if prefix == "watch-voice-file":
        if hashlib.sha256(row.data).hexdigest() != row.sha256 or len(row.data) != row.size_bytes:
            return _tool_error_result(
                request_id=request_id,
                code="watch_voice_integrity_failed",
                message="Watch original integrity failed",
            )
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=row.id,
            data=row.data,
            media_type=row.media_type,
            filename_prefix="watch-voice",
        )
    receipt = {
        "event_id": row.id,
        "source_file_id": row.file_id,
        "revision": row.revision,
        "audio_sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "recorded_at": None,
        "dropbox_modified_at": row.dropbox_modified_at.isoformat(),
        "gate_received_at": row.received_at.isoformat(),
        "transcribed_at": row.transcribed_at.isoformat() if row.transcribed_at else None,
        "transcript": row.transcript,
        "transcription_state": row.transcription_state,
        "delivery_state": row.delivery_state,
        "issue_url": row.issue_url,
        "github_received_at": row.github_received_at.isoformat()
        if row.github_received_at
        else None,
        "last_error": row.last_error,
        "model": row.model,
    }
    if row.model == "gpt-4o-transcribe":
        receipt["recovery"] = await protected_recovery_receipt(session, row)
        receipt["post_payment"] = await protected_post_payment_receipt(session, row)
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": json.dumps(receipt, ensure_ascii=False)}],
            "structuredContent": context,
            "isError": False,
        },
    )


def _parse_latest_call_selector(raw_task_id: str) -> str | None:
    selectors = {
        "call:latest": "audio",
        "call:latest-file": "file",
        "call:latest-transcript": "transcript",
    }
    if raw_task_id in selectors:
        return selectors[raw_task_id]
    if raw_task_id.startswith("call:latest"):
        raise ValueError("invalid latest call selector")
    return None


async def _latest_phone_call_tool_result(
    *,
    request_id: Any,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
    payload_kind: str,
) -> JsonObject:
    recording = await session.scalar(
        select(PhoneCallRecording)
        .where(
            PhoneCallRecording.workspace_key == settings.gate_workspace_id,
            PhoneCallRecording.project_id.in_(principal.access_scope.project_ids),
        )
        .order_by(
            PhoneCallRecording.received_at.desc(),
            PhoneCallRecording.id.desc(),
        )
        .limit(1)
    )
    if recording is None:
        return _tool_error_result(
            request_id=request_id,
            code="phone_call_archive_empty",
            message="No archived mobile call is available in the allowed scope",
        )
    recording_id = str(recording.id)
    context = {
        "task_id": recording_id,
        "task_ref": recording_id[:8],
        "status": f"phone_call_{recording.transcription_status}",
        "reply_required": False,
        "account": {
            "id": recording_id,
            "alias": "phone_calls",
            "label": "PHONE CALLS",
        },
        "conversation": {
            "id": recording_id,
            "chat_id": "phone-call",
            "business_connection_id": None,
        },
        "counterparty": {
            "platform": "phone_call_dropbox",
            "platform_user_id": None,
            "display_name": None,
            "username": None,
            "label": None,
        },
        "messages": [],
        "count": 0,
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
    }
    effective_media_type = _effective_phone_call_media_type(
        recording.media_type,
        recording.filename,
    )
    if payload_kind == "transcript":
        if (
            recording.transcription_status != "transcribed"
            or recording.transcript_text is None
        ):
            return _tool_error_result(
                request_id=request_id,
                code=(
                    recording.transcription_reason_code
                    or "phone_call_transcription_pending"
                ),
                message="The latest mobile call has no completed transcript",
            )
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "content": [{"type": "text", "text": recording.transcript_text}],
                "structuredContent": context,
                "isError": False,
            },
        )
    if payload_kind == "file":
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=recording_id,
            data=recording.data,
            media_type=effective_media_type,
            filename_prefix="phone-call",
        )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=recording.data,
        media_type=effective_media_type,
    )


def _effective_phone_call_media_type(media_type: str, filename: str) -> str:
    if media_type.startswith("audio/"):
        return media_type
    guessed = mimetypes.guess_type(filename)[0]
    if guessed is not None and guessed.startswith("audio/"):
        return guessed
    if filename.casefold().endswith(".m4a"):
        return "audio/mp4"
    return media_type


def _parse_latest_voice_file_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest-file:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice file selector")
    return alias


def _parse_latest_voice_transcript_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest-transcript:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice transcript selector")
    return alias


def _voice_account_selector_scope(
    account_selector: str,
) -> tuple[str, str | None]:
    country_code = account_selector if account_selector in {"ru", "by"} else None
    return account_selector, country_code


def _voice_account_selector_filter(account_selector: str) -> Any:
    exact_alias, country_code = _voice_account_selector_scope(account_selector)
    if country_code is None:
        return Account.alias == exact_alias
    return or_(
        Account.alias == exact_alias,
        Account.country_code == country_code,
        Account.alias.endswith(f"_{country_code}"),
    )


async def _latest_voice_archive_tool_result(
    *,
    request_id: Any,
    account_alias: str,
    principal: Principal,
    session: AsyncSession,
    file_payload: bool = False,
    settings: Settings | None = None,
    transcript_payload: bool = False,
) -> JsonObject:
    result = await session.execute(
        select(TelegramVoiceArchive, Account)
        .join(Account, Account.id == TelegramVoiceArchive.account_id)
        .where(
            _voice_account_selector_filter(account_alias),
            Account.id.in_(principal.access_scope.account_ids),
            TelegramVoiceArchive.account_id.in_(
                principal.access_scope.account_ids
            ),
            TelegramVoiceArchive.project_id.in_(
                principal.access_scope.project_ids
            ),
        )
        .order_by(
            TelegramVoiceArchive.received_at.desc(),
            TelegramVoiceArchive.id.desc(),
        )
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_archive_empty",
            message="No archived Telegram voice is available for this account",
        )
    archive, account = row
    archive_id = str(archive.id)
    context = {
        "task_id": archive_id,
        "task_ref": archive_id[:8],
        "status": "archived_voice",
        "reply_required": False,
        "account": {
            "id": str(account.id),
            "alias": account.alias,
            "label": account.alias.upper(),
        },
        "conversation": {
            "id": archive_id,
            "chat_id": "archived-voice",
            "business_connection_id": None,
        },
        "counterparty": {
            "platform": "telegram",
            "platform_user_id": None,
            "display_name": None,
            "username": None,
            "label": None,
        },
        "messages": [],
        "count": 0,
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
    }
    if transcript_payload:
        if settings is None:
            raise ValueError("settings are required for transcription")
        return await _voice_transcript_tool_result(
            request_id=request_id,
            context=context,
            archive=archive,
            session=session,
            settings=settings,
        )
    if file_payload:
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=archive_id,
            data=archive.data,
            media_type=archive.media_type,
        )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=archive.data,
        media_type=archive.media_type,
    )


class _ArchivedVoiceDownloader:
    def __init__(self, voice: DownloadedVoice) -> None:
        self._voice = voice

    async def download(
        self,
        _reference: TelegramVoiceReference,
    ) -> DownloadedVoice:
        return self._voice


async def _voice_transcript_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    archive: TelegramVoiceArchive,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if (
        archive.transcription_status == "transcribed"
        and archive.transcript_text is not None
    ):
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "content": [
                    {
                        "type": "text",
                        "text": archive.transcript_text,
                    },
                ],
                "structuredContent": context,
                "isError": False,
            },
        )
    if archive.transcription_status is not None:
        return _tool_error_result(
            request_id=request_id,
            code=(
                archive.transcription_reason_code
                or "telegram_voice_transcription_blocked"
            ),
            message="Stored OpenAI voice transcription has no text",
        )

    api_key = settings.openai_api_key.get_secret_value()
    if not settings.gate_telegram_voice_transcription_enabled or not api_key:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_transcription_unavailable",
            message="OpenAI voice transcription is not ready",
        )
    if session.bind is None:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_transcription_unavailable",
            message="Voice transcription database binding is unavailable",
        )

    occurred_at = datetime.now(UTC)
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        service = TelegramVoiceTranscriptionService(
            downloader=_ArchivedVoiceDownloader(
                DownloadedVoice(
                    data=archive.data,
                    filename=archive.filename,
                    media_type=archive.media_type,
                )
            ),
            normalizer=TelegramVoiceNormalizer(),
            budget=SqlBillingBudgetService(sessions),
            speech_to_text=OpenAITranscriptionClient(
                config=OpenAITranscriptionConfig(
                    api_key=api_key,
                    enabled=True,
                    model=settings.gate_telegram_voice_transcription_model,
                    base_url=settings.openai_api_base_url,
                ),
                http=http_client,
            ),
        )
        try:
            transcription = await service.transcribe(
                TranscribeTelegramVoiceCommand(
                    voice=TelegramVoiceReference(
                        file_id=f"archive-{archive.id}",
                        file_unique_id=str(archive.id),
                        declared_size_bytes=archive.size_bytes,
                        duration_seconds=archive.duration_seconds,
                    ),
                    workspace_key=settings.gate_workspace_id,
                    connector_ref="telegram_business",
                    reservation_key=f"mcp-transcription-v2:{archive.id}",
                    period_key=occurred_at.strftime("%Y-%m"),
                    requested_microusd=max(
                        100,
                        archive.duration_seconds
                        * settings.gate_telegram_voice_reserve_microusd_per_second,
                    ),
                    occurred_at=occurred_at,
                    language="ru",
                    enabled=True,
                )
            )
        except Exception:
            return _tool_error_result(
                request_id=request_id,
                code="telegram_voice_transcription_failed",
                message="OpenAI voice transcription failed closed",
            )

    archive.transcription_status = transcription.outcome.value
    archive.transcript_text = transcription.alpha_input_text
    archive.transcript_language = "ru"
    archive.transcription_model = (
        settings.gate_telegram_voice_transcription_model
    )
    archive.transcription_reason_code = transcription.reason_code
    archive.transcribed_at = occurred_at
    await session.commit()

    if (
        transcription.outcome is not TelegramVoiceOutcome.TRANSCRIBED
        or transcription.alpha_input_text is None
    ):
        return _tool_error_result(
            request_id=request_id,
            code=transcription.reason_code,
            message="OpenAI voice transcription did not return text",
        )
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": transcription.alpha_input_text,
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


def _parse_reply_context_selector(
    raw_task_id: str,
) -> tuple[UUID, UUID | None, tuple[UUID, int] | None]:
    attachment_parts = raw_task_id.split(":attachment:")
    if len(attachment_parts) == 2 and all(attachment_parts):
        task_raw, selection_raw = attachment_parts
        message_parts = selection_raw.rsplit(":", maxsplit=1)
        if (
            len(message_parts) != 2
            or not all(message_parts)
            or not message_parts[1].isdigit()
        ):
            raise ValueError("invalid attachment selector")
        attachment_index = int(message_parts[1])
        if attachment_index < 0 or attachment_index > 32:
            raise ValueError("invalid attachment selector")
        return (
            UUID(task_raw),
            None,
            (UUID(message_parts[0]), attachment_index),
        )
    if len(attachment_parts) != 1:
        raise ValueError("invalid attachment selector")

    voice_parts = raw_task_id.split(":voice:")
    if len(voice_parts) == 1:
        return UUID(raw_task_id), None, None
    if len(voice_parts) != 2 or not all(voice_parts):
        raise ValueError("invalid voice selector")
    return UUID(voice_parts[0]), UUID(voice_parts[1]), None


async def _attachment_reply_context_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
    attachment_index: int,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    visible_message_ids = {
        item.get("id")
        for item in context.get("messages", [])
        if isinstance(item, dict)
    }
    if str(message_id) not in visible_message_ids:
        return _tool_error_result(
            request_id=request_id,
            code="attachment_message_not_in_context",
            message="Selected message is not in the scoped reply context",
        )

    conversation = context["conversation"]
    result = await session.execute(
        select(Message, TelegramUpdate, GateEvent, TelegramReceipt)
        .outerjoin(
            TelegramUpdate,
            (
                (TelegramUpdate.bot_id == Message.bot_id)
                & (TelegramUpdate.update_id == Message.telegram_update_id)
            ),
        )
        .outerjoin(GateEvent, GateEvent.id == Message.gate_event_id)
        .outerjoin(
            CanonicalMessage,
            CanonicalMessage.id == Message.canonical_message_id,
        )
        .outerjoin(
            TelegramReceipt,
            (
                (TelegramReceipt.id == CanonicalMessage.receipt_id)
                & (TelegramReceipt.bot_key == "telegram_business")
            ),
        )
        .where(
            Message.id == message_id,
            Message.account_id.in_(principal.access_scope.account_ids),
            Message.project_id.in_(principal.access_scope.project_ids),
            Message.chat_id == conversation["chat_id"],
            Message.business_connection_id.is_not_distinct_from(
                conversation["business_connection_id"]
            ),
            Message.source == "telegram",
        )
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="attachment_message_not_found",
            message="Selected Telegram message was not found in the allowed scope",
        )
    message, telegram_update, gate_event, telegram_receipt = row
    receipt_payload = (
        validated_telegram_receipt_payload(
            telegram_receipt.payload,
            receipt_update_id=telegram_receipt.update_id,
            message_update_id=message.telegram_update_id,
            event_type=message.event_type,
            telegram_message_id=message.telegram_message_id,
            chat_id=message.chat_id,
            business_connection_id=message.business_connection_id,
        )
        if telegram_receipt is not None
        else None
    )
    attachments = extract_telegram_attachments(
        telegram_update.payload if telegram_update is not None else None,
        gate_event.payload if gate_event is not None else None,
        message.payload,
        receipt_payload,
    )
    if attachment_index >= len(attachments):
        return _tool_error_result(
            request_id=request_id,
            code="attachment_not_found",
            message="Selected message has no attachment at that index",
        )
    attachment = attachments[attachment_index]
    if not attachment.download_available:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_attachment_cloud_limit_exceeded",
            message=(
                "Telegram's hosted Bot API cannot download this attachment "
                "because its declared size exceeds 20 MB"
            ),
        )
    if not settings.telegram_bot_token:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_attachment_download_unavailable",
            message="Telegram attachment download is not configured",
        )

    try:
        data, media_type = await _download_telegram_attachment(
            attachment=attachment,
            bot_token=settings.telegram_bot_token,
        )
    except TelegramAttachmentError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Telegram attachment download failed closed",
        )
    return _attachment_content_tool_result(
        request_id=request_id,
        context=context,
        message_id=message_id,
        attachment_index=attachment_index,
        attachment=attachment,
        data=data,
        media_type=media_type,
    )


async def _download_telegram_attachment(
    *,
    attachment: TelegramAttachmentReference,
    bot_token: str,
) -> tuple[bytes, str]:
    api = "https://api.telegram.org"
    timeout = httpx.Timeout(60.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            metadata_response = await http_client.post(
                f"{api}/bot{bot_token}/getFile",
                json={"file_id": attachment.file_id},
            )
            metadata = metadata_response.json()
            if (
                metadata_response.is_error
                or not isinstance(metadata, dict)
                or metadata.get("ok") is not True
                or not isinstance(metadata.get("result"), dict)
            ):
                raise TelegramAttachmentError(
                    "telegram_attachment_get_file_rejected"
                )
            file_metadata = metadata["result"]
            file_size = file_metadata.get("file_size")
            if (
                type(file_size) is int
                and file_size > TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
            ):
                raise TelegramAttachmentError(
                    "telegram_attachment_cloud_limit_exceeded"
                )
            file_path = file_metadata.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                raise TelegramAttachmentError(
                    "telegram_attachment_file_path_missing"
                )
            path = PurePosixPath(file_path.strip().lstrip("/"))
            if any(part in {".", ".."} for part in path.parts):
                raise TelegramAttachmentError(
                    "telegram_attachment_file_path_invalid"
                )
            safe_path = quote(path.as_posix(), safe="/")
            data = bytearray()
            media_type = attachment.mime_type
            async with http_client.stream(
                "GET",
                f"{api}/file/bot{bot_token}/{safe_path}",
            ) as response:
                response.raise_for_status()
                header_type = response.headers.get("content-type")
                if isinstance(header_type, str) and header_type.strip():
                    media_type = header_type.split(";", maxsplit=1)[0].strip()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if (
                        len(data)
                        > TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
                    ):
                        raise TelegramAttachmentError(
                            "telegram_attachment_cloud_limit_exceeded"
                        )
    except TelegramAttachmentError:
        raise
    except (httpx.HTTPError, ValueError):
        raise TelegramAttachmentError(
            "telegram_attachment_download_failed"
        ) from None
    if not data:
        raise TelegramAttachmentError("telegram_attachment_empty")
    return bytes(data), media_type or "application/octet-stream"


def _attachment_content_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
    attachment_index: int,
    attachment: TelegramAttachmentReference,
    data: bytes,
    media_type: str,
) -> JsonObject:
    encoded = base64.b64encode(data).decode("ascii")
    if media_type.startswith("image/"):
        content_block: JsonObject = {
            "type": "image",
            "data": encoded,
            "mimeType": media_type,
        }
    elif media_type.startswith("audio/"):
        content_block = {
            "type": "audio",
            "data": encoded,
            "mimeType": media_type,
        }
    else:
        filename = quote(attachment.filename, safe="")
        content_block = {
            "type": "resource",
            "resource": {
                "uri": (
                    f"gate://telegram-attachment/{message_id}/"
                    f"{attachment_index}/{filename}"
                ),
                "mimeType": media_type,
                "blob": encoded,
            },
        }
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Owner-selected Telegram attachment: "
                        f"{attachment.filename} ({media_type}, {len(data)} bytes)."
                    ),
                },
                content_block,
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


async def _voice_reply_context_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    visible_message_ids = {
        item.get("id")
        for item in context.get("messages", [])
        if isinstance(item, dict)
    }
    if str(message_id) not in visible_message_ids:
        return _tool_error_result(
            request_id=request_id,
            code="voice_message_not_in_context",
            message="Selected message is not in the scoped reply context",
        )

    result = await session.execute(
        select(Message, TelegramUpdate, GateEvent)
        .outerjoin(
            TelegramUpdate,
            (
                (TelegramUpdate.bot_id == Message.bot_id)
                & (TelegramUpdate.update_id == Message.telegram_update_id)
            ),
        )
        .outerjoin(GateEvent, GateEvent.id == Message.gate_event_id)
        .where(
            Message.id == message_id,
            Message.account_id.in_(principal.access_scope.account_ids),
            Message.project_id.in_(principal.access_scope.project_ids),
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.source == "telegram",
        )
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="voice_message_not_found",
            message="Selected outbound Telegram message was not found",
        )
    message, telegram_update, gate_event = row
    voice = _extract_voice_from_linked_payloads(
        telegram_update.payload if telegram_update is not None else None,
        gate_event.payload if gate_event is not None else None,
        message.payload,
    )
    if voice is None:
        voice = await _find_voice_by_exact_telegram_identity(
            session=session,
            message=message,
        )
    if voice is None:
        return _tool_error_result(
            request_id=request_id,
            code="selected_message_is_not_voice",
            message="Selected message has no retrievable Telegram audio reference",
        )
    if not settings.telegram_bot_token:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_download_unavailable",
            message="Telegram voice download is not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            downloaded = await TelegramVoiceDownloader(
                config=TelegramVoiceDownloadConfig(
                    bot_token=settings.telegram_bot_token,
                ),
                http=http_client,
            ).download(voice)
    except (TelegramVoiceError, ValueError):
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_download_failed",
            message="Telegram voice download failed closed",
        )

    media_type = (
        downloaded.media_type
        if downloaded.media_type.startswith("audio/")
        else "audio/ogg"
    )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=downloaded.data,
        media_type=media_type,
    )


def _extract_voice_from_linked_payloads(
    *payloads: JsonObject | None,
) -> TelegramVoiceReference | None:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        try:
            envelope = extract_telegram_voice(payload)
        except TelegramVoiceError:
            envelope = None
        if envelope is not None:
            return envelope.voice

        for message in _telegram_audio_messages(payload):
            for field in ("voice", "audio", "document"):
                raw_media = message.get(field)
                if not isinstance(raw_media, dict):
                    continue
                if field == "document":
                    mime_type = raw_media.get("mime_type")
                    if not isinstance(mime_type, str) or not mime_type.startswith(
                        "audio/"
                    ):
                        continue
                reference = _telegram_audio_reference(raw_media)
                if reference is not None:
                    return reference
    return None


async def _find_voice_by_exact_telegram_identity(
    *,
    session: AsyncSession,
    message: Message,
) -> TelegramVoiceReference | None:
    if message.telegram_message_id is None:
        return None

    event_result = await session.execute(
        select(GateEvent.payload)
        .where(
            GateEvent.bot_id == message.bot_id,
            GateEvent.chat_id == message.chat_id,
            GateEvent.telegram_message_id == message.telegram_message_id,
        )
        .order_by(GateEvent.received_at.desc())
        .limit(5)
    )
    voice = _extract_voice_from_linked_payloads(*event_result.scalars().all())
    if voice is not None:
        return voice

    anchor = message.sent_at or message.received_at or message.created_at
    update_result = await session.execute(
        select(TelegramUpdate.payload)
        .where(
            TelegramUpdate.bot_id == message.bot_id,
            TelegramUpdate.received_at >= anchor - timedelta(days=1),
            TelegramUpdate.received_at <= anchor + timedelta(days=1),
        )
        .order_by(TelegramUpdate.received_at.desc())
        .limit(1000)
    )
    for payload in update_result.scalars():
        if not isinstance(payload, dict):
            continue
        for raw_message in _telegram_audio_messages(payload):
            if _same_telegram_message(raw_message=raw_message, message=message):
                return _extract_voice_from_linked_payloads(raw_message)
    return None


def _same_telegram_message(*, raw_message: JsonObject, message: Message) -> bool:
    raw_chat = raw_message.get("chat")
    return (
        raw_message.get("message_id") == message.telegram_message_id
        and isinstance(raw_chat, dict)
        and str(raw_chat.get("id")) == message.chat_id
    )


def _telegram_audio_messages(payload: JsonObject) -> tuple[JsonObject, ...]:
    messages: list[JsonObject] = []
    for key in (
        "message",
        "business_message",
        "edited_message",
        "edited_business_message",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            messages.append(value)
    result = payload.get("result")
    if isinstance(result, dict):
        messages.append(result)
    if any(isinstance(payload.get(key), dict) for key in ("voice", "audio", "document")):
        messages.append(payload)
    return tuple(messages)


def _telegram_audio_reference(raw_media: JsonObject) -> TelegramVoiceReference | None:
    file_id = raw_media.get("file_id")
    file_unique_id = raw_media.get("file_unique_id")
    file_size = raw_media.get("file_size")
    duration = raw_media.get("duration")
    if (
        not isinstance(file_id, str)
        or not file_id.strip()
        or not isinstance(file_unique_id, str)
        or not file_unique_id.strip()
        or (file_size is not None and (type(file_size) is not int or file_size <= 0))
        or type(duration) is not int
        or duration <= 0
    ):
        return None
    try:
        return TelegramVoiceReference(
            file_id=file_id,
            file_unique_id=file_unique_id,
            declared_size_bytes=file_size,
            duration_seconds=duration,
        )
    except TelegramVoiceError:
        return None


def _voice_audio_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    data: bytes,
    media_type: str,
) -> JsonObject:
    if not data or not media_type.startswith("audio/"):
        raise ValueError("invalid audio content")
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": "Owner-selected Telegram voice attached.",
                },
                {
                    "type": "audio",
                    "data": base64.b64encode(data).decode("ascii"),
                    "mimeType": media_type,
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


def _voice_file_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    archive_id: str,
    data: bytes,
    media_type: str,
    filename_prefix: str = "telegram-voice",
) -> JsonObject:
    if not data or not media_type.startswith("audio/"):
        raise ValueError("invalid audio content")
    extension = "ogg" if media_type in {"audio/ogg", "audio/opus"} else "audio"
    file_payload = {
        "version": 1,
        "filename": f"{filename_prefix}-{archive_id}.{extension}",
        "mime_type": media_type,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        file_payload,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


async def _beta_export_space_checkpoint_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if (
        not isinstance(raw_ref, str)
        or not raw_ref
        or len(raw_ref) > 128
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )

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
    installation = next(
        (
            item
            for item in installations
            if item.installation_key == raw_ref
        ),
        None,
    )
    if (
        installation is None
        or installation.state != AlphaInstallationState.ACTIVE
    ):
        return _tool_error_result(
            request_id=request_id,
            code="beta_checkpoint_installation_not_active",
            message="The exact Beta Space is not active",
        )

    try:
        payload = await export_beta_space_checkpoint(
            session,
            installation_ref=raw_ref,
        )
    except BetaCheckpointExportError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Beta checkpoint export failed closed",
        )
    return _tool_result(
        payload,
        (
            f"Encrypted Beta checkpoint: {payload['sequence_start']}-"
            f"{payload['sequence_end']}; events={payload['event_count']}"
        ),
        request_id,
    )


async def _beta_get_provider_probe_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    del principal, session
    if set(arguments) != {"confirmed"} or arguments.get("confirmed") is not True:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="confirmed=true is required",
        )
    if (
        settings.gate_env != "production"
        or not settings.gate_beta_connector_enabled
    ):
        return _tool_error_result(
            request_id=request_id,
            code="beta_probe_not_production_ready",
            message="Beta provider probe is not production-ready",
        )
    try:
        async with build_telegram_http_client() as http_client:
            probe = await get_beta_provider_probe(
                settings=settings,
                http_client=http_client,
            )
            if (
                not probe["webhook"]["exact_match"]
                or not probe["webhook"]["actual_origin_match"]
                or "callback_query" not in probe["webhook"]["allowed_updates"]
                or not probe["identity"]["ordinary_group_messages_available"]
                or probe["identity"]["privacy_mode_enabled"]
            ):
                return _tool_error_result(
                    request_id=request_id,
                    code="beta_binding_provider_not_exact",
                    message="Beta provider state is not exact",
                )
            wedding_activation = {
                "state": "binding_creation_disabled",
                "candidate_count": 0,
                "binding_created": False,
                "prompt_required": False,
                "prompt_delivery": "not_attempted",
                "private_identifiers_exposed": False,
                "private_payload_exposed": False,
                "consent_digest_exposed": False,
            }
            return _tool_result(
                {"probe": probe, "activation": wedding_activation},
                (
                    "Beta provider probe: "
                    f"activation={wedding_activation['state']}; "
                    "candidate_count=0; binding_creation=disabled; "
                    "prompt_delivery=not_attempted"
                ),
                request_id,
            )
    except TelegramWebhookStatusError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.code,
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


async def _alpha_get_webhook_status_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if (
        not isinstance(raw_ref, str)
        or not raw_ref
        or len(raw_ref) > 128
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )

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
    installation = next(
        (
            item
            for item in installations
            if item.installation_key == raw_ref
        ),
        None,
    )
    if installation is None:
        return _tool_error_result(
            request_id=request_id,
            code="alpha_installation_not_found",
            message="Alpha installation not found in the allowed scope",
        )
    if (
        installation.state is not AlphaInstallationState.ACTIVE
        or not installation.connector_enabled
    ):
        return _tool_error_result(
            request_id=request_id,
            code="alpha_webhook_installation_not_ready",
            message="Alpha installation is not active and connector-ready",
        )

    try:
        async with httpx.AsyncClient() as http_client:
            webhook = await get_alpha_webhook_status(
                settings=settings,
                http_client=http_client,
            )
    except TelegramWebhookStatusError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.code,
        )

    payload = {
        "installation_ref": raw_ref,
        "webhook": webhook,
    }
    match = "exact" if webhook["exact_match"] else "mismatch"
    return _tool_result(
        payload,
        f"Alpha webhook: {match}; pending="
        f"{webhook['pending_update_count']}",
        request_id,
    )


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


async def _alpha_get_test_group_presence_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if not isinstance(raw_ref, str) or arguments.get("confirmed") is not True:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )
    try:
        async with httpx.AsyncClient() as http_client:
            presence = await observe_owner_test_presence(
                session=session,
                settings=settings,
                http_client=http_client,
                workspace_key=principal.access_scope.workspace_id,
                installation_ref=raw_ref,
            )
    except AlphaOwnerTestRuntimeError as exc:
        await session.rollback()
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Alpha owner-test presence probe failed closed",
        )
    payload = presence.payload()
    return _tool_result(
        payload,
        (
            f"Alpha test group {payload['group_label']}: "
            f"{payload['state']}; members={payload['member_count']}"
        ),
        request_id,
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
    if raw_ref == AQUB_GENERAL_CUBE0_RECOVERY_INSTALLATION_REF:
        if request is None:
            return _tool_error_result(
                request_id=request_id,
                code="aqub_general_cube0_recovery_request_missing",
                message="AQUB General Cube 0 recovery failed closed",
            )

        async def replay_update(update: JsonObject) -> JsonObject:
            secret = settings.telegram_aqub_webhook_secret.get_secret_value()
            if not secret:
                raise AQUBGeneralReleaseReplayError(
                    "aqub_general_cube0_recovery_webhook_secret_missing"
                )
            transport = httpx.ASGITransport(
                app=request.app,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://gate.internal",
                timeout=40.0,
            ) as internal_client:
                response = await internal_client.post(
                    AQUB_TELEGRAM_WEBHOOK_PATH,
                    json=update,
                    headers={
                        "X-Telegram-Bot-Api-Secret-Token": secret,
                        "X-AQUB-Recovery-Diagnostic": "cube0-v1",
                    },
                )
            if response.status_code != 200:
                raise AQUBGeneralReleaseReplayError(
                    "aqub_general_cube0_recovery_ingress_failed"
                )
            try:
                value = response.json()
            except ValueError as exc:
                raise AQUBGeneralReleaseReplayError(
                    "aqub_general_cube0_recovery_ingress_failed"
                ) from exc
            if not isinstance(value, dict):
                raise AQUBGeneralReleaseReplayError(
                    "aqub_general_cube0_recovery_ingress_failed"
                )
            return {str(key): item for key, item in value.items()}

        try:
            payload = await recover_aqub_general_cube0(
                session=session,
                installation_ref=raw_ref,
                turn_ref=raw_turn,
                expected_candidate_sha256=raw_digest,
                confirmed=True,
                replay_update=replay_update,
            )
        except AQUBGeneralReleaseReplayError as exc:
            await session.rollback()
            return _tool_error_result(
                request_id=request_id,
                code=exc.code,
                message="AQUB General Cube 0 recovery failed closed",
            )
        return _tool_result(
            payload,
            (
                f"AQUB General Cube 0 recovery: {payload['state']}; "
                f"replay={payload['replay']}"
            ),
            request_id,
        )

    if raw_ref == AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF:
        try:
            async with httpx.AsyncClient() as http_client:
                payload = await send_aqub_general_release_replay(
                    session=session,
                    settings=settings,
                    http_client=http_client,
                    installation_ref=raw_ref,
                    turn_ref=raw_turn,
                    expected_candidate_sha256=raw_digest,
                    confirmed=True,
                )
        except AQUBGeneralReleaseReplayError as exc:
            await session.rollback()
            return _tool_error_result(
                request_id=request_id,
                code=exc.code,
                message="AQUB General release replay failed closed",
            )
        return _tool_result(
            payload,
            (
                f"AQUB General release replay: {payload['state']}; "
                f"replay={payload['replay']}"
            ),
            request_id,
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


async def _alpha_verify_review_replay_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    raw_turn = arguments.get("turn_ref")
    raw_digest = arguments.get("expected_candidate_sha256")
    if (
        not isinstance(raw_ref, str)
        or not isinstance(raw_turn, str)
        or not isinstance(raw_digest, str)
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "installation_ref, turn_ref and expected_candidate_sha256 "
                "are required"
            ),
        )
    if raw_ref == AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF:
        try:
            payload = await verify_aqub_general_release_replay(
                session=session,
                installation_ref=raw_ref,
                turn_ref=raw_turn,
                expected_candidate_sha256=raw_digest,
            )
        except AQUBGeneralReleaseReplayError as exc:
            await session.rollback()
            return _tool_error_result(
                request_id=request_id,
                code=exc.code,
                message="AQUB General replay verification failed closed",
            )
        return _tool_result(
            payload,
            (
                f"AQUB General replay proof: {payload['state']}; "
                f"replay={payload['replay']}"
            ),
            request_id,
        )

    try:
        payload = await verify_owner_test_review_replay(
            session=session,
            workspace_key=principal.access_scope.workspace_id,
            installation_ref=raw_ref,
            turn_ref=raw_turn,
            expected_candidate_sha256=raw_digest,
        )
    except (AlphaOwnerTestRuntimeError, ValueError) as exc:
        await session.rollback()
        code = exc.code if isinstance(exc, AlphaOwnerTestRuntimeError) else str(exc)
        return _tool_error_result(
            request_id=request_id,
            code=code,
            message="Alpha owner-test replay verification failed closed",
        )
    return _tool_result(
        payload,
        (
            f"Alpha review replay proof: {payload['state']}; "
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
            "Return one scoped RU/BY conversation as a chronological text, voice "
            "and attachment timeline. Attachment metadata is listed without a "
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
            "Read gate_get_reply_context first and pass its context_snapshot as "
            "expected_context_snapshot. A changed dialog requires a new draft and approval. "
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
                "expected_context_snapshot": {"type": "string", "pattern": "^dialog-v1:[0-9a-f]{64}$"},
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
            "Use expected_payload_sha256 from the displayed formatting preview; "
            "required for explicit-mode drafts/captions. Legacy rows without a preview retain the old contract. "
            "Send the exact saved draft through its originating RU/BY Telegram "
            "Business account. Call only after showing the account, recipient and "
            "final text to Mark and receiving explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "format": "uuid"},
                "confirmed": {"type": "boolean", "const": True},
                "expected_payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
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
            "Read gate_get_reply_context first and pass its context_snapshot as "
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
                "expected_context_snapshot": {"type": "string", "pattern": "^dialog-v1:[0-9a-f]{64}$"},
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
            "Never split or reformat during delivery. "
            f"Queue {MIN_SEQUENCE_DRAFTS} to {MAX_SEQUENCE_DRAFTS} saved drafts "
            "for the same RU/BY recipient. "
            "Call only after showing Mark the account, recipient and every final "
            "message in order and receiving one explicit confirmation of the whole "
            "sequence. The first item becomes due immediately and later items no "
            "sooner than 60 seconds after the preceding confirmed delivery."
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
    }:
        return _tool_error_result(
            request_id=request_id, code="invalid_arguments",
            message="Unknown arguments are not supported",
        )
    expected_payload_sha256 = arguments.get("expected_payload_sha256")
    if "expected_payload_sha256" in arguments and not isinstance(expected_payload_sha256, str):
        return _tool_error_result(request_id=request_id, code="invalid_arguments", message="expected_payload_sha256 must be a SHA-256 string")
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


def _tool_result(content: JsonObject, text: str, request_id: Any) -> JsonObject:
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": text}],
            "structuredContent": content,
            "isError": False,
        },
    )


async def _dialog_context_error_result(
    *, request_id: Any, code: str, message: str, principal: Principal,
    session: AsyncSession, task_id: UUID | None = None, draft_id: UUID | None = None,
) -> JsonObject:
    await session.rollback()
    current_context = None
    try:
        if "gate:inbox:read" not in principal.scopes:
            return _tool_error_result(
                request_id=request_id, code=code, message=message,
                details={"sent": False, "current_context": None},
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
    return _tool_error_result(
        request_id=request_id, code=code, message=message,
        details={"sent": False, "current_context": current_context},
    )


def _tool_error_result(*, request_id: Any, code: str, message: str,
                       details: JsonObject | None = None) -> JsonObject:
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": message}],
            "structuredContent": {"error": code, **(details or {})},
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
            f"- groups_within_capacity: "
            f"{alpha['groups_within_capacity']}",
            f"- all_active_bindings_exact: "
            f"{alpha['all_active_bindings_exact']}",
            f"- reviewed_model_release_ready: "
            f"{alpha['reviewed_model_release_ready']}",
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
    beta_katya = snapshot["beta_katya_space"]
    lines.extend(
        [
            "",
            "Beta — Зубы Кати:",
            f"- state: {beta_katya['state']}",
            f"- production_ready: {beta_katya['production_ready']}",
            f"- binding_state: {beta_katya['binding_state']}",
            f"- consent_count: {beta_katya['consent_count']}",
            f"- fact_count: {beta_katya['fact_count']}",
            f"- delivery_sent_count: {beta_katya['delivery_sent_count']}",
            f"- delivery_unknown_count: {beta_katya['delivery_unknown_count']}",
        ]
    )
    beta_wedding = snapshot["beta_wedding_space"]
    lines.extend(
        [
            "",
            "Beta — Свадьба в Минске:",
            f"- state: {beta_wedding['state']}",
            f"- production_ready: {beta_wedding['production_ready']}",
            f"- binding_state: {beta_wedding['binding_state']}",
            f"- fact_count: {beta_wedding['fact_count']}",
            f"- reply_sent_count: {beta_wedding['reply_sent_count']}",
            f"- reply_unknown_count: {beta_wedding['reply_unknown_count']}",
            f"- delivery_enabled: {beta_wedding['gates']['delivery_enabled']}",
        ]
    )
    voice = snapshot["telegram_voice"]
    lines.extend(
        [
            "",
            "Telegram voice transcription:",
            f"- state: {voice['state']}",
            f"- enabled: {voice['enabled']}",
            f"- api_key_configured: {voice['api_key_configured']}",
            f"- model: {voice['model']}",
            f"- entitlement_active: {voice['entitlement_active']}",
            f"- settled_count: {voice['settled_count']}",
            f"- open_count: {voice['open_count']}",
            f"- unknown_count: {voice['unknown_count']}",
            f"- reconciliation_required: "
            f"{voice['reconciliation_required']}",
            f"- received_count: {voice['received_count']}",
            f"- processing_count: {voice['processing_count']}",
            f"- pending_count: {voice['pending_count']}",
            f"- active_processing_count: {voice['active_processing_count']}",
            f"- stale_processing_count: {voice['stale_processing_count']}",
            f"- nonterminal_archive_missing_count: "
            f"{voice['nonterminal_archive_missing_count']}",
            f"- nonterminal_archive_pending_count: "
            f"{voice['nonterminal_archive_pending_count']}",
            f"- nonterminal_archive_transcribed_count: "
            f"{voice['nonterminal_archive_transcribed_count']}",
            f"- nonterminal_archive_other_count: "
            f"{voice['nonterminal_archive_other_count']}",
            f"- nonterminal_transcript_complete_count: "
            f"{voice['nonterminal_transcript_complete_count']}",
            f"- nonterminal_summary_complete_count: "
            f"{voice['nonterminal_summary_complete_count']}",
            f"- transport_unknown_archive_missing_count: "
            f"{voice['transport_unknown_archive_missing_count']}",
            f"- transport_unknown_archive_present_count: "
            f"{voice['transport_unknown_archive_present_count']}",
            f"- transport_unknown_transcript_complete_count: "
            f"{voice['transport_unknown_transcript_complete_count']}",
            f"- transport_unknown_summary_complete_count: "
            f"{voice['transport_unknown_summary_complete_count']}",
            f"- delivered_count: {voice['delivered_count']}",
            f"- retry_count: {voice['retry_count']}",
            f"- dead_letter_count: {voice['dead_letter_count']}",
            f"- dead_letter_by_code: {voice.get('dead_letter_by_code', {})}",
            f"- last_error_code: {voice['last_error_code'] or 'none'}",
            f"- budget_settled: $"
            f"{voice['settled_microusd'] / 1_000_000:.6f}",
            f"- budget_reserved: $"
            f"{voice['reserved_microusd'] / 1_000_000:.6f}",
            f"- budget_remaining: $"
            f"{voice['remaining_microusd'] / 1_000_000:.6f}",
            f"- telegram_delivery_allowed: "
            f"{voice['telegram_delivery_allowed']}",
        ]
    )
    parity = snapshot["telegram_business_text_parity"]
    lines.extend(
        [
            "",
            "Telegram Business text parity:",
            f"- state: {parity['state']}",
            f"- matched_count: {parity['matched_count']}",
            f"- pending_count: {parity['pending_count']}",
            f"- mismatch_count: {parity['mismatch_count']}",
            f"- covered_accounts: {parity['covered_account_count']}/"
            f"{parity['required_account_count']}",
            f"- cutover_ready: {parity['cutover_ready']}",
            "- private_content_exposed: False",
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
    active_installation_count = await alpha_canary_active_installation_count(
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
    binding_readiness = alpha_release_binding_readiness(
        discovered_installations=installation_count,
        group_capacity=settings.gate_alpha_group_capacity,
        active_installations=active_installation_count,
        exact_active_bindings=active_binding_count,
        reviewed_model_bindings=model.active_model_bindings,
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
            fixture_binding_active=(
                binding_readiness["all_active_bindings_exact"]
                and model.active_model_bindings == 0
            ),
            telegram_delivery_blocked=not (
                settings.gate_alpha_delivery_enabled
                and binding_readiness["reviewed_model_release_ready"]
            ),
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
        "groups_within_capacity": binding_readiness["groups_within_capacity"],
        "all_active_bindings_exact": binding_readiness[
            "all_active_bindings_exact"
        ],
        "reviewed_model_release_ready": binding_readiness[
            "reviewed_model_release_ready"
        ],
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
        for attachment in message.get("attachments", []):
            availability = (
                "available on request"
                if attachment["download_available"]
                else attachment["download_state"]
            )
            lines.append(
                "attachment "
                f"{attachment['index']}: {attachment['filename']} "
                f"({attachment['mime_type']}, "
                f"{attachment['size_bytes'] or 'size unknown'}, "
                f"{availability}); selector={attachment['selector']}"
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


