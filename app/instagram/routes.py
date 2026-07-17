"""Fail-closed Meta webhook ingress for Instagram."""

from __future__ import annotations

import hmac

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.instagram.config import InstagramRuntimeConfig
from app.instagram.command_routes import router as instagram_command_router
from app.instagram.sink import MayakiInstagramEventSink, MayakiInstagramSinkError
from app.instagram.webhooks import (
    InstagramWebhookError,
    normalize_webhook,
    verify_meta_signature,
)

router = APIRouter()
webhook_router = APIRouter(prefix="/webhooks/instagram", tags=["instagram"])


@webhook_router.get("")
async def verify_instagram_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> PlainTextResponse:
    config = InstagramRuntimeConfig.from_env()
    if (
        not config.webhook_verification_ready
        or mode != "subscribe"
        or not challenge
        or not verify_token
        or not hmac.compare_digest(verify_token, config.webhook_verify_token)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return PlainTextResponse(challenge)


@webhook_router.post("")
async def receive_instagram_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, int | bool]:
    config = InstagramRuntimeConfig.from_env()
    if not config.webhook_verification_ready:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    body = await request.body()
    try:
        verify_meta_signature(
            body=body,
            signature_header=signature,
            app_secret=config.app_secret,
        )
        events = normalize_webhook(
            body=body,
            workspace_key="mark-personal",
            account_alias_by_provider_id={
                account.instagram_user_id: alias for alias, account in config.accounts.items()
            },
        )
    except InstagramWebhookError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    if not events:
        return {"ok": True, "accepted": 0, "delivered": 0}
    try:
        async with httpx.AsyncClient() as http:
            delivered = await MayakiInstagramEventSink(
                config=config,
                http=http,
            ).deliver(events)
    except (MayakiInstagramSinkError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"ok": True, "accepted": len(events), "delivered": delivered}


router.include_router(webhook_router)
router.include_router(instagram_command_router)
