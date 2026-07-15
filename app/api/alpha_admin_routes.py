"""Owner-admin control plane for Alpha installation lifecycle."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_bearer
from app.api.deps import get_db_session
from app.application.alpha_lifecycle import (
    AlphaInstallationActionCommand,
    AlphaInstallationView,
    AlphaLifecycleError,
    AlphaLifecycleService,
    ApproveAlphaInstallationCommand,
)
from app.core.config import Settings, get_settings
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository

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
