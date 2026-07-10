"""Application factory for the Gate backend."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.routes import router
from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router
from app.core.config import get_settings
from app.services.telegram_webhook import reconcile_telegram_webhook_with_retries

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run non-blocking production startup reconciliation."""

    reconciliation_task = asyncio.create_task(
        reconcile_telegram_webhook_with_retries(get_settings())
    )
    try:
        yield
    finally:
        if not reconciliation_task.done():
            reconciliation_task.cancel()
        with suppress(asyncio.CancelledError):
            await reconciliation_task


async def telegram_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    """Acknowledge valid but unsupported Telegram updates instead of retrying forever."""

    is_unsupported_telegram_update = (
        request.url.path == "/telegram/webhook"
        and exc.status_code == 400
        and exc.detail == "Unsupported Telegram update"
    )
    if not is_unsupported_telegram_update:
        return await http_exception_handler(request, exc)

    update_id: Any = None
    update_types: list[str] = []
    try:
        payload = await request.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        update_id = payload.get("update_id")
        update_types = sorted(str(key) for key in payload if key != "update_id")

    logger.warning(
        "Acknowledging unsupported Telegram update: update_id=%s types=%s",
        update_id,
        ",".join(update_types) or "unknown",
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "ignored": True,
            "reason": "unsupported_update",
            "update_id": update_id,
            "update_types": update_types,
        },
    )


def create_app() -> FastAPI:
    """Create the FastAPI app for cloud deployment."""

    app = FastAPI(
        title="Gate",
        description="Cloud-first communication gateway backend.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_exception_handler(StarletteHTTPException, telegram_http_exception_handler)
    app.include_router(sprint10_router)
    app.include_router(sprint9_router)
    app.include_router(router)
    return app


app = create_app()
