"""Bounded private-Mark Actions drain. Credentials and payload never leave this request."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_database_runtime
from app.core.config import Settings, get_settings
from app.core.github_actions_oidc import GitHubOidcError
from app.core.watch_voice_actions_oidc import authenticate
from app.infrastructure.watch_voice_actions import drain, result
from app.infrastructure.watch_voice_github import WatchVoiceError

router = APIRouter(prefix="/internal/watch-voice")
settings_dependency = Depends(get_settings)


class EmptyDrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/drain")
async def drain_watch_voice(
    body: EmptyDrainRequest,
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    job_token: Annotated[str | None, Header(alias="X-Gate-Mark-Job-Token")] = None,
    settings: Settings = settings_dependency,
) -> JSONResponse:
    try:
        claims = await authenticate(authorization=authorization, settings=settings)
    except GitHubOidcError as exc:
        raise HTTPException(
            status_code=401,
            detail=exc.code,
            headers={
                "WWW-Authenticate": "Bearer",
                "Cache-Control": "no-store",
            },
        ) from None
    if not job_token or not 1 <= len(job_token) <= 1_024 or job_token != job_token.strip():
        raise HTTPException(
            status_code=401,
            detail="watch_voice_job_token_required",
            headers={"Cache-Control": "no-store"},
        )
    # Dedicated timeout, no automatic retry, no shared-client auth/defaults.
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        try:
            payload = await drain(
                settings=settings,
                factory=get_database_runtime(request).session_factory,
                http=http,
                claims=claims,
                job_token=job_token,
            )
        except WatchVoiceError:
            payload = result("deferred")
        except Exception:
            # No raw exception, HTTP response, credential or private selector is logged.
            payload = result("deferred")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})
