# ruff: noqa
"""Application factory for the Gate backend."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
import httpx

from app.api.alpha_admin_routes import router as alpha_admin_router
from app.api.beta_checkpoint_routes import router as beta_checkpoint_router
from app.api.contact_snapshot_routes import router as contact_snapshot_router
from app.api.dropbox_call_routes import router as dropbox_call_router
from app.api.health_routes import router as health_router
from app.api.mcp_metadata_routes import router as mcp_metadata_router
from app.api.mcp_transcription_routes import router as mcp_router
from app.api.owner_one_off_transcription import router as owner_one_off_transcription_router
from app.api.routes import router
from app.api.sprint12_routes import router as sprint12_router
from app.api.telegram_ingress import include_telegram_ingress
from app.application.worker_health import (
    REPLY_SEQUENCE_WORKER,
    TELEGRAM_WORKER,
    WorkerHealthRegistry,
)
from app.core.config import Settings, get_settings
from app.infrastructure.become_connector_binding import (
    reconcile_become_connector_binding,
)
from app.infrastructure.beta_connector_binding import (
    reconcile_beta_connector_binding,
)
from app.infrastructure.beta_space import reconcile_beta_katya_space_binding
from app.infrastructure.database import database_runtime_lifespan
from app.infrastructure.readiness import build_readiness_service
from app.services.telegram import TelegramClient, build_telegram_http_client
from app.services.telegram_alpha_webhook import (
    reconcile_alpha_telegram_webhook_with_retries,
)
from app.services.telegram_beta_webhook import (
    reconcile_beta_telegram_webhook_with_retries,
)
from app.services.telegram_become_webhook import (
    reconcile_become_telegram_webhook_with_retries,
)
from app.services.telegram_webhook import reconcile_telegram_webhook_with_retries
from app.worker.alpha_transcriptions import run_alpha_transcription_worker_loop
from app.worker.reply_sequences import run_reply_sequence_worker_loop
from app.worker.dropbox_calls import run_dropbox_call_worker_loop
from app.worker.telegram_processing import run_telegram_worker_loop

logger = logging.getLogger(__name__)


async def _reconcile_all_telegram_webhooks(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> None:
    await asyncio.gather(
        reconcile_telegram_webhook_with_retries(settings, http_client),
        reconcile_alpha_telegram_webhook_with_retries(settings, http_client),
        reconcile_beta_telegram_webhook_with_retries(settings, http_client),
        reconcile_become_telegram_webhook_with_retries(settings, http_client),
    )


def _configured_value(settings: object, attribute: str) -> bool:
    value = getattr(settings, attribute, None)
    if value is None:
        return False
    reveal = getattr(value, "get_secret_value", None)
    if callable(reveal):
        return bool(reveal())
    return bool(value)


def _alpha_transcription_configured(settings: object) -> bool:
    return all(
        _configured_value(settings, attribute)
        for attribute in (
            "openai_api_key",
            "dropbox_call_app_key",
            "dropbox_call_app_secret",
            "dropbox_call_refresh_token",
            "dropbox_call_account_id",
        )
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own process-scoped resources and reconcile the Telegram webhook."""

    settings = get_settings()
    telegram_worker_enabled = getattr(settings, "gate_telegram_worker_enabled", False)
    reply_sequence_worker_enabled = getattr(
        settings,
        "gate_reply_sequence_worker_enabled",
        False,
    )
    dropbox_call_worker_enabled = getattr(
        settings,
        "gate_dropbox_call_intake_enabled",
        False,
    )
    alpha_transcription_enabled = _alpha_transcription_configured(settings)
    worker_health = WorkerHealthRegistry()
    worker_health.register(
        TELEGRAM_WORKER,
        enabled=telegram_worker_enabled,
    )
    worker_health.register(
        REPLY_SEQUENCE_WORKER,
        enabled=reply_sequence_worker_enabled,
    )
    _app.state.worker_health_registry = worker_health
    async with database_runtime_lifespan(settings.database_url) as database_runtime:
        _app.state.database_runtime = database_runtime
        try:
            if getattr(settings, "gate_become_connector_enabled", False):
                await reconcile_become_connector_binding(
                    settings,
                    database_runtime.session_factory,
                )
            if getattr(settings, "gate_beta_connector_enabled", False):
                await reconcile_beta_connector_binding(
                    settings,
                    database_runtime.session_factory,
                )
                await reconcile_beta_katya_space_binding(
                    settings,
                    database_runtime.session_factory,
                )
            async with build_telegram_http_client() as http_client:
                reconciliation_task: asyncio.Task[None] | None = None
                worker_task: asyncio.Task[None] | None = None
                reply_sequence_task: asyncio.Task[None] | None = None
                dropbox_call_task: asyncio.Task[None] | None = None
                alpha_transcription_task: asyncio.Task[None] | None = None
                try:
                    api_base_url = os.getenv(
                        "TELEGRAM_API_BASE_URL",
                        "https://api.telegram.org",
                    )
                    _app.state.telegram_client = TelegramClient(
                        credential=settings.telegram_bot_token,
                        api_base_url=api_base_url,
                        http_client=http_client,
                    )
                    if getattr(
                        settings,
                        "gate_beta_katya_delivery_enabled",
                        False,
                    ):
                        _app.state.beta_telegram_client = TelegramClient(
                            credential=settings.telegram_beta_bot_token.get_secret_value(),
                            api_base_url=api_base_url,
                            http_client=http_client,
                        )
                    _app.state.readiness_service = build_readiness_service(
                        database_runtime.engine
                    )
                    reconciliation_task = asyncio.create_task(
                        _reconcile_all_telegram_webhooks(settings, http_client)
                    )
                    if alpha_transcription_enabled:
                        alpha_transcription_task = asyncio.create_task(
                            run_alpha_transcription_worker_loop(
                                settings=settings,
                                session_factory=database_runtime.session_factory,
                            )
                        )
                    if telegram_worker_enabled:
                        worker_task = asyncio.create_task(
                            run_telegram_worker_loop(
                                settings,
                                health_reporter=worker_health,
                            )
                        )
                    if reply_sequence_worker_enabled:
                        reply_sequence_task = asyncio.create_task(
                            run_reply_sequence_worker_loop(
                                settings=settings,
                                session_factory=database_runtime.session_factory,
                                telegram=_app.state.telegram_client,
                                health_reporter=worker_health,
                            )
                        )
                    if dropbox_call_worker_enabled:
                        dropbox_call_task = asyncio.create_task(
                            run_dropbox_call_worker_loop(
                                settings=settings,
                                session_factory=database_runtime.session_factory,
                            )
                        )
                    yield
                finally:
                    try:
                        if alpha_transcription_task is not None:
                            if not alpha_transcription_task.done():
                                alpha_transcription_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await alpha_transcription_task
                        if dropbox_call_task is not None:
                            if not dropbox_call_task.done():
                                dropbox_call_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await dropbox_call_task
                        if reply_sequence_task is not None:
                            if not reply_sequence_task.done():
                                reply_sequence_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await reply_sequence_task
                        if worker_task is not None:
                            if not worker_task.done():
                                worker_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await worker_task
                        if reconciliation_task is not None:
                            if not reconciliation_task.done():
                                reconciliation_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await reconciliation_task
                    finally:
                        if hasattr(_app.state, "readiness_service"):
                            delattr(_app.state, "readiness_service")
                        if hasattr(_app.state, "telegram_client"):
                            delattr(_app.state, "telegram_client")
        finally:
            delattr(_app.state, "database_runtime")
            delattr(_app.state, "worker_health_registry")


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
        description="Cloud-first FastAPI communication gateway backend.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_exception_handler(StarletteHTTPException, telegram_http_exception_handler)
    app.add_exception_handler(Exception, telegram_unhandled_exception_handler)
    app.include_router(health_router)
    app.include_router(contact_snapshot_router)
    app.include_router(dropbox_call_router)
    app.include_router(mcp_metadata_router)
    app.include_router(mcp_router)
    app.include_router(owner_one_off_transcription_router)
    app.include_router(alpha_admin_router)
    app.include_router(beta_checkpoint_router)
    include_telegram_ingress(app)
    app.include_router(sprint12_router)
    app.include_router(router)
    return app


app = create_app()