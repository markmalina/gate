"""Internal authenticated route for MAYAKI Instagram commands."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.instagram.command_gateway import (
    InstagramCommandGatewayError,
    execute_authenticated_command,
)
from app.instagram.commands import JsonInstagramCommandResultStore
from app.instagram.config import InstagramRuntimeConfig
from app.instagram.meta import MetaInstagramClient, MetaInstagramCommandExecutor

router = APIRouter(prefix="/internal/instagram", tags=["instagram-internal"])


@router.post("/commands")
async def receive_instagram_command(
    request: Request,
    authorization: str = Header(default=""),
    timestamp: str = Header(default="", alias="X-Mayaki-Timestamp"),
    signature: str = Header(default="", alias="X-Mayaki-Signature"),
    idempotency_key: str = Header(default="", alias="X-Mayaki-Idempotency-Key"),
) -> dict[str, object]:
    config = InstagramRuntimeConfig.from_env()
    if not config.command_bridge_ready or not config.delivery_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    raw_body = (await request.body()).decode()
    try:
        async with httpx.AsyncClient() as http:
            return await execute_authenticated_command(
                executor=MetaInstagramCommandExecutor(
                    client=MetaInstagramClient(config=config, http=http),
                    result_store=JsonInstagramCommandResultStore(config.command_result_file),
                ),
                raw_body=raw_body,
                authorization=authorization,
                timestamp=timestamp,
                signature=signature,
                idempotency_key=idempotency_key,
                bearer_token=config.command_bridge_bearer_token,
                hmac_secret=config.command_bridge_hmac_secret,
            )
    except InstagramCommandGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
