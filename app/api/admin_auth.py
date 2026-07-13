"""Shared bearer protection for Gate admin routes."""

import hmac

from fastapi import HTTPException, status

from app.core.config import Settings


def require_admin_bearer(*, authorization: str | None, settings: Settings) -> None:
    """Require the configured admin bearer token without exposing it."""

    if not settings.admin_bearer_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin bearer token is not configured",
        )

    scheme, _, token = (authorization or "").partition(" ")
    invalid_token = (
        scheme.casefold() != "bearer"
        or not token
        or not hmac.compare_digest(token, settings.admin_bearer_token)
    )
    if invalid_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin bearer token",
        )
