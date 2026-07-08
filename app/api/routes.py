"""HTTP routes for Gate."""

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter()
settings_dependency = Depends(get_settings)


@router.get("/health")
async def health(settings: Settings = settings_dependency) -> dict[str, str]:
    """Return a lightweight health response suitable for Railway checks."""

    return {
        "status": "ok",
        "service": "gate",
        "environment": settings.gate_env,
        "default_mode": settings.gate_default_mode,
    }


@router.get("/deployment/fingerprint")
async def deployment_fingerprint(settings: Settings = settings_dependency) -> dict[str, str]:
    """Expose non-secret deployment metadata for cloud verification."""

    return {
        "service": settings.deployment_service,
        "environment": settings.gate_env,
        "commit_sha": settings.deployment_sha,
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
    }
