"""Application factory for the Gate backend."""

from fastapi import FastAPI

from app.api.routes import router
from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router


def create_app() -> FastAPI:
    """Create the FastAPI app for cloud deployment."""

    app = FastAPI(
        title="Gate",
        description="Cloud-first communication gateway backend.",
        version="0.1.0",
    )
    app.include_router(sprint10_router)
    app.include_router(sprint9_router)
    app.include_router(router)
    return app


app = create_app()
