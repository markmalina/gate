"""Application factory for the Gate backend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import router
from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router
from app.core.config import get_settings
from app.services.telegram_webhook import reconcile_telegram_webhook_with_retries


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


def create_app() -> FastAPI:
    """Create the FastAPI app for cloud deployment."""

    app = FastAPI(
        title="Gate",
        description="Cloud-first communication gateway backend.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(sprint10_router)
    app.include_router(sprint9_router)
    app.include_router(router)
    return app


app = create_app()
