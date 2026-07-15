"""Owner-admin control plane for Alpha installation lifecycle."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_bearer
from app.api.deps import get_db_session
from app.application.alpha_canary import ALPHA_DISCLOSURE_PREFIX
from app.application.alpha_lifecycle import (
    AlphaInstallationActionCommand,
    AlphaInstallationView,
    AlphaLifecycleError,
    AlphaLifecycleService,
    ApproveAlphaInstallationCommand,
)
from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    alpha_model_readiness_payload,
)
from app.core.config import Settings, get_settings
from app.infrastructure.alpha_canary import (
    alpha_canary_audit_counts,
    alpha_canary_binding_count,
    alpha_canary_installation_count,
)
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.infrastructure.alpha_model_status import alpha_model_operational_status

router = APIRouter(prefix="/admin/alpha", tags=["alpha-admin"])
settings_dependency = Depends(get_settings)
session_dependency = Depends(get_db_session)


class AlphaApprovalRequest(BaseModel):
    workspace_key: str = Field(min_length=1, max_length=128)
    deployment_key: str = Field(min_length=1, max_length=128)
    revision_key: str = Field(min_length=1, max_length=128)
    max_turns_per_window: int = Field(default=5, ge=1, le=10_000)
    rate_window_seconds: int = Field(default=60, ge=1, le=86_400)
    max_tokens_per_turn: int = Field(default=2048, ge=1, le=100_000)


class AlphaActionRequest(BaseModel):
    workspace_key: str = Field(min_length=1, max_length=128)


@router.get("/canary-status")
async def get_alpha_canary_status(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    workspace_key: Annotated[str, Query(min_length=1, max_length=128)] = "mark-personal",
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> dict[str, Any]:
    """Return a redacted activation checklist; never reveal credentials or group id."""

    require_admin_bearer(authorization=authorization, settings=settings)
    installation_count = await alpha_canary_installation_count(
        session,
        workspace_key=workspace_key,
    )
    exact_binding_count = await alpha_canary_binding_count(
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
        )
    )
    checks = {
        "telegram_worker_enabled": settings.gate_telegram_worker_enabled,
        "connector_enabled": settings.gate_alpha_connector_enabled,
        "shadow_worker_enabled": settings.gate_alpha_shadow_worker_enabled,
        "canary_enabled": settings.gate_alpha_canary_enabled,
        "delivery_enabled": settings.gate_alpha_delivery_enabled,
        "privacy_mode_confirmed": settings.gate_alpha_privacy_mode_confirmed,
        "bot_username_configured": bool(settings.telegram_alpha_bot_username),
        "bot_token_configured": bool(
            settings.telegram_alpha_bot_token.get_secret_value()
        ),
        "webhook_secret_configured": bool(
            settings.telegram_alpha_webhook_secret.get_secret_value()
        ),
        "one_group_discovered": installation_count == 1,
        "one_active_exact_revision": exact_binding_count == 1,
    }
    return {
        "code_ready": True,
        "activation_ready": all(checks.values()),
        "checks": checks,
        "allowed_updates": ["message", "my_chat_member"],
        "disclosure_copy": ALPHA_DISCLOSURE_PREFIX.strip(),
        "active_installations": exact_binding_count,
        "audit_evidence": audit_counts,
        "model_shadow": model_shadow,
    }


@router.get("/installations")
async def list_alpha_installations(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    workspace_key: Annotated[str, Query(min_length=1, max_length=128)] = "mark-personal",
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> dict[str, Any]:
    """Return privacy-bounded status for owner-visible Alpha installations."""

    require_admin_bearer(authorization=authorization, settings=settings)
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installations = await service.list_status(workspace_key)
    except AlphaLifecycleError as exc:
        raise _http_error(exc) from exc
    return {"installations": [_serialize(item) for item in installations]}


@router.post("/installations/{installation_key}/approve")
async def approve_alpha_installation(
    installation_key: str,
    request: AlphaApprovalRequest,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> dict[str, Any]:
    """Bind one pending group to one active deployment and validated revision."""

    require_admin_bearer(authorization=authorization, settings=settings)
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installation = await service.approve(
            ApproveAlphaInstallationCommand(
                workspace_key=request.workspace_key,
                installation_key=installation_key,
                deployment_key=request.deployment_key,
                revision_key=request.revision_key,
                max_turns_per_window=request.max_turns_per_window,
                rate_window_seconds=request.rate_window_seconds,
                max_tokens_per_turn=request.max_tokens_per_turn,
            )
        )
        await session.commit()
    except (AlphaLifecycleError, ValueError) as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return {"installation": _serialize(installation)}


@router.post("/installations/{installation_key}/pause")
async def pause_alpha_installation(
    installation_key: str,
    request: AlphaActionRequest,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> dict[str, Any]:
    """Pause an active group and revoke every unused delivery grant."""

    return await _apply_action(
        action="pause",
        installation_key=installation_key,
        workspace_key=request.workspace_key,
        authorization=authorization,
        settings=settings,
        session=session,
    )


@router.post("/installations/{installation_key}/revoke")
async def revoke_alpha_installation(
    installation_key: str,
    request: AlphaActionRequest,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> dict[str, Any]:
    """Permanently revoke a group installation and every unused delivery grant."""

    return await _apply_action(
        action="revoke",
        installation_key=installation_key,
        workspace_key=request.workspace_key,
        authorization=authorization,
        settings=settings,
        session=session,
    )


async def _apply_action(
    *,
    action: str,
    installation_key: str,
    workspace_key: str,
    authorization: str | None,
    settings: Settings,
    session: AsyncSession,
) -> dict[str, Any]:
    require_admin_bearer(authorization=authorization, settings=settings)
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    command = AlphaInstallationActionCommand(
        workspace_key=workspace_key,
        installation_key=installation_key,
    )
    try:
        if action == "pause":
            installation = await service.pause(command)
        else:
            installation = await service.revoke(command)
        await session.commit()
    except (AlphaLifecycleError, ValueError) as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return {"installation": _serialize(installation)}


def _serialize(installation: AlphaInstallationView) -> dict[str, Any]:
    return {
        "installation_key": installation.installation_key,
        "workspace_key": installation.workspace_key,
        "connector_key": installation.connector_key,
        "connector_enabled": installation.connector_enabled,
        "dialog_kind": installation.dialog_kind.value,
        "dialog_label": installation.dialog_label,
        "state": installation.state.value,
        "deployment_key": installation.deployment_key,
        "revision_key": installation.revision_key,
        "limits": {
            "max_turns_per_window": installation.max_turns_per_window,
            "rate_window_seconds": installation.rate_window_seconds,
            "max_tokens_per_turn": installation.max_tokens_per_turn,
        },
        "usage": {
            "reserved_turns": installation.reserved_turns,
            "recorded_tokens": installation.recorded_tokens,
        },
    }


def _http_error(exc: AlphaLifecycleError | ValueError) -> HTTPException:
    if isinstance(exc, AlphaLifecycleError):
        not_found = exc.code.endswith("_not_found")
        return HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND if not_found else status.HTTP_409_CONFLICT
            ),
            detail={"code": exc.code, "message": exc.message},
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "alpha_invalid_request", "message": str(exc)},
    )
