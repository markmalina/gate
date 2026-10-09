"""Process liveness, deployment readiness, and redacted worker health routes."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.admin_auth import require_admin_bearer
from app.api.deps import get_database_runtime
from app.application.readiness import (
    CheckReadinessService,
    ReadinessComponentStatus,
    ReadinessFailureCode,
    ReadinessResult,
)
from app.application.worker_health import (
    WorkerHealthRegistry,
    WorkerRuntimeState,
)
from app.core.config import Settings, get_settings
from app.infrastructure.reply_cutover import observe_reply_cutover
from app.services.telegram_aqub_webhook import (
    AQUBProviderErrorCode,
    AQUBProviderReadinessState,
)

router = APIRouter()
settings_dependency = Depends(get_settings)


class ReadinessChecksResponse(BaseModel):
    """Public component states."""

    model_config = ConfigDict(extra="forbid")
    database: ReadinessComponentStatus
    migrations: ReadinessComponentStatus


class ReadinessResponse(BaseModel):
    """Explicit public schema for deployment readiness."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_ready"]
    reason: ReadinessFailureCode | None
    checks: ReadinessChecksResponse


class WorkerHealthResponse(BaseModel):
    """One bounded worker observation without identities or exception details."""

    model_config = ConfigDict(extra="forbid")
    name: str
    enabled: bool
    state: WorkerRuntimeState
    consecutive_failures: int
    heartbeat_age_seconds: float | None
    last_error_code: str | None
    last_failure_age_seconds: float | None


class AQUBProviderHealthResponse(BaseModel):
    """Owner-only provider readiness without credentials or provider data."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool
    configured: bool
    identity_verified: bool
    can_join_groups: bool
    privacy_mode_enabled: bool
    webhook_configured: bool
    ready: bool
    error_code: AQUBProviderErrorCode | Literal["provider_state_unavailable"] | None


class OperationsHealthResponse(BaseModel):
    """Owner-only aggregate worker health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["healthy", "degraded"]
    reason: Literal["worker_registry_unavailable"] | None
    workers: tuple[WorkerHealthResponse, ...]


def _get_readiness_service(request: Request) -> CheckReadinessService | None:
    service = getattr(request.app.state, "readiness_service", None)
    return service if isinstance(service, CheckReadinessService) else None


def _get_worker_health_registry(request: Request) -> WorkerHealthRegistry | None:
    registry = getattr(request.app.state, "worker_health_registry", None)
    return registry if isinstance(registry, WorkerHealthRegistry) else None


def _get_aqub_provider_readiness(
    request: Request,
) -> AQUBProviderReadinessState | None:
    readiness = getattr(request.app.state, "aqub_provider_readiness", None)
    return (
        readiness
        if isinstance(readiness, AQUBProviderReadinessState)
        else None
    )


@router.get("/live")
async def live() -> dict[str, str]:
    """Report that the ASGI process can serve requests."""

    return {"status": "alive", "service": "gate"}


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(request: Request) -> JSONResponse:
    """Report DB/schema readiness without leaking infrastructure details."""

    service = _get_readiness_service(request)
    result = (
        await service.execute()
        if service is not None
        else ReadinessResult(
            ready=False,
            database=ReadinessComponentStatus.UNKNOWN,
            migrations=ReadinessComponentStatus.UNKNOWN,
            reason=ReadinessFailureCode.CHECK_FAILED,
        )
    )
    payload = ReadinessResponse(
        status="ready" if result.ready else "not_ready",
        reason=result.reason,
        checks=ReadinessChecksResponse(
            database=result.database,
            migrations=result.migrations,
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/admin/ops/aqub-provider",
    response_model=AQUBProviderHealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AQUBProviderHealthResponse},
    },
)
async def aqub_provider_health(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
) -> JSONResponse:
    """Return only bounded AQUB provider readiness booleans and a safe code."""

    require_admin_bearer(authorization=authorization, settings=settings)
    state = _get_aqub_provider_readiness(request)
    if state is None:
        payload = AQUBProviderHealthResponse(
            enabled=False,
            configured=False,
            identity_verified=False,
            can_join_groups=False,
            privacy_mode_enabled=False,
            webhook_configured=False,
            ready=False,
            error_code="provider_state_unavailable",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    snapshot = state.snapshot()
    payload = AQUBProviderHealthResponse(
        enabled=snapshot.enabled,
        configured=snapshot.configured,
        identity_verified=snapshot.identity_verified,
        can_join_groups=snapshot.can_join_groups,
        privacy_mode_enabled=snapshot.privacy_mode_enabled,
        webhook_configured=snapshot.webhook_configured,
        ready=snapshot.ready,
        error_code=snapshot.error_code,
    )
    return JSONResponse(
        status_code=(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if snapshot.enabled and not snapshot.ready
            else status.HTTP_200_OK
        ),
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/admin/ops/health",
    response_model=OperationsHealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": OperationsHealthResponse},
    },
)
async def operations_health(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
) -> JSONResponse:
    """Return owner-only redacted worker degradation evidence."""

    require_admin_bearer(authorization=authorization, settings=settings)
    registry = _get_worker_health_registry(request)
    if registry is None:
        unavailable = OperationsHealthResponse(
            status="degraded",
            reason="worker_registry_unavailable",
            workers=(),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=unavailable.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    snapshot = registry.snapshot(observed_at=datetime.now(UTC))
    payload = OperationsHealthResponse(
        status=snapshot.status,
        reason=None,
        workers=tuple(
            WorkerHealthResponse(
                name=worker.name,
                enabled=worker.enabled,
                state=worker.state,
                consecutive_failures=worker.consecutive_failures,
                heartbeat_age_seconds=worker.heartbeat_age_seconds,
                last_error_code=worker.last_error_code,
                last_failure_age_seconds=worker.last_failure_age_seconds,
            )
            for worker in snapshot.workers
        ),
    )
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if snapshot.status == "healthy"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/ops/reply-cutover")
async def reply_cutover_health(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
) -> JSONResponse:
    """Report redacted database observations; never assert remote fleet quiescence."""

    require_admin_bearer(authorization=authorization, settings=settings)
    try:
        runtime = get_database_runtime(request)
        async with runtime.session_factory() as session:
            observation = await observe_reply_cutover(session)
        payload = {
            "status": "observed",
            "deployment_sha": settings.deployment_sha,
            "schema_revisions": list(observation.schema_revisions),
            "reserved": observation.reserved,
            "sending": observation.sending,
            "unknown": observation.unknown,
            "send_enabled": settings.gate_send_enabled,
            "guard_contract": "dialog-v1",
        }
        status_code = status.HTTP_200_OK
    except Exception:
        payload = {"status": "unavailable", "reason": "reply_cutover_observation_failed"}
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )
