# ruff: noqa
"""Application factory for the Gate backend."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.mcp_metadata_routes import router as mcp_metadata_router
from app.api.mcp_routes import router as mcp_router
from app.api.routes import router
from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router
from app.api.sprint11b_routes import router as sprint11b_router
from app.api.sprint12_routes import router as sprint12_router
from app.api.sprint14_routes import router as sprint14_router
from app.api.sprint14b_routes import router as sprint14b_router
from app.api.sprint15b_routes import router as sprint15b_router
from app.api.sprint16_raw_routes import router as sprint16_raw_router
from app.api.sprint16d_clean_inbox_routes import router as sprint16d_clean_inbox_router
from app.api.sprint17_send_draft_routes import router as sprint17_send_draft_router
from app.api.sprint18b_draft_ux_routes import router as sprint18b_draft_ux_router
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

    payload = await _safe_request_json(request)
    if not isinstance(payload, dict):
        return await http_exception_handler(request, exc)

    update_id = payload.get("update_id")
    if type(update_id) is not int:
        return await http_exception_handler(request, exc)

    update_types = _telegram_update_types(payload)
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


async def telegram_unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> Response:
    """Acknowledge poison Telegram updates so Telegram can drain its retry queue."""

    if request.url.path != "/telegram/webhook":
        logger.exception("Unhandled request exception")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    payload = await _safe_request_json(request)
    update_id = payload.get("update_id") if isinstance(payload, dict) else None
    update_types = _telegram_update_types(payload) if isinstance(payload, dict) else []
    logger.exception(
        "Acknowledging failing Telegram update: update_id=%s types=%s error_type=%s",
        update_id if update_id is not None else "unknown",
        ",".join(update_types) or "unknown",
        exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "ignored": True,
            "reason": "handler_exception",
            "error_type": exc.__class__.__name__,
            "update_id": update_id,
            "update_types": update_types,
        },
    )


async def _safe_request_json(request: Request) -> object | None:
    try:
        return await request.json()
    except Exception:
        return None


def _telegram_update_types(payload: dict) -> list[str]:
    return sorted(str(key) for key in payload if key != "update_id")


def create_app() -> FastAPI:
    """Create the FastAPI app for cloud deployment."""

    app = FastAPI(
        title="Gate",
        description="Cloud-first communication gateway backend.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_exception_handler(StarletteHTTPException, telegram_http_exception_handler)
    app.add_exception_handler(Exception, telegram_unhandled_exception_handler)
    app.include_router(mcp_metadata_router)
    app.include_router(mcp_router)
    app.include_router(sprint18b_draft_ux_router)
    app.include_router(sprint17_send_draft_router)
    app.include_router(sprint16d_clean_inbox_router)
    app.include_router(sprint16_raw_router)
    app.include_router(sprint15b_router)
    app.include_router(sprint14b_router)
    app.include_router(sprint14_router)
    app.include_router(sprint12_router)
    app.include_router(sprint11b_router)
    app.include_router(sprint10_router)
    app.include_router(sprint9_router)
    app.include_router(router)
    return app


app = create_app()
