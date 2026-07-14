"""Process liveness and deployment readiness routes."""

from typing import Literal

from app.application.readiness import (
    CheckReadinessService,
    ReadinessComponentStatus,
    ReadinessFailureCode,
    ReadinessResult,
)
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

router = APIRouter()


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


def _get_readiness_service(request: Request) -> CheckReadinessService | None:
    service = getattr(request.app.state, "readiness_service", None)
    return service if isinstance(service, CheckReadinessService) else None


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
