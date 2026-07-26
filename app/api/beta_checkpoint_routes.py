"""GitHub OIDC-bound encrypted Beta checkpoint batch endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.core.github_actions_oidc import (
    GitHubOidcError,
    authenticate_beta_checkpoint_oidc,
)
from app.infrastructure.beta_checkpoint_batches import (
    BetaCheckpointBatchError,
    acknowledge_beta_checkpoint_batch,
    next_beta_checkpoint_batch,
)

router = APIRouter(prefix="/internal/beta/checkpoints")
settings_dependency = Depends(get_settings)
session_dependency = Depends(get_db_session)

_INSTALLATION_PATTERN = r"^installation-[a-f0-9]{32}$"
_WORKSPACE_PATTERN = r"^beta-user-[a-z0-9-]+-[0-9]{3}$"
_GROUP_PATTERN = r"^beta-group-[a-z0-9-]+-[0-9]{3}$"


class CheckpointNextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_ref: str = Field(
        min_length=1, max_length=128, pattern=_INSTALLATION_PATTERN
    )
    user_workspace_id: str = Field(
        min_length=1, max_length=128, pattern=_WORKSPACE_PATTERN
    )
    group_id: str = Field(
        min_length=1, max_length=128, pattern=_GROUP_PATTERN
    )


class CheckpointAckRequest(CheckpointNextRequest):
    sequence_start: int = Field(ge=1)
    sequence_end: int = Field(ge=1)
    ciphertext_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    github_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


async def _authorize(
    authorization: str | None,
    settings: Settings,
) -> None:
    try:
        await authenticate_beta_checkpoint_oidc(
            authorization=authorization,
            settings=settings,
        )
    except GitHubOidcError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.code,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/next")
async def next_checkpoint(
    request: CheckpointNextRequest,
    authorization: Annotated[
        str | None, Header(alias="Authorization")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> Response:
    """Create or replay one encrypted batch without advancing its cursor."""

    await _authorize(authorization, settings)
    try:
        async with session.begin():
            payload = await next_beta_checkpoint_batch(
                session,
                installation_ref=request.installation_ref,
                expected_user_workspace_id=request.user_workspace_id,
                expected_group_id=request.group_id,
            )
    except BetaCheckpointBatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.code,
        ) from exc
    if payload is None:
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/ack")
async def acknowledge_checkpoint(
    request: CheckpointAckRequest,
    authorization: Annotated[
        str | None, Header(alias="Authorization")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = session_dependency,
) -> JSONResponse:
    """Advance the exact cursor only after an encrypted Git commit exists."""

    await _authorize(authorization, settings)
    try:
        async with session.begin():
            payload = await acknowledge_beta_checkpoint_batch(
                session,
                installation_ref=request.installation_ref,
                expected_user_workspace_id=request.user_workspace_id,
                expected_group_id=request.group_id,
                sequence_start=request.sequence_start,
                sequence_end=request.sequence_end,
                ciphertext_sha256=request.ciphertext_sha256,
                github_commit_sha=request.github_commit_sha,
            )
    except BetaCheckpointBatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.code,
        ) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )
