"""Reconcile Telegram webhook configuration with the current public service URL."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_public_webhook_url(environ: Mapping[str, str]) -> str | None:
    """Build the public Telegram webhook URL from Railway or explicit runtime settings."""

    raw_value = (
        environ.get("PUBLIC_BASE_URL")
        or environ.get("RAILWAY_PUBLIC_DOMAIN")
        or environ.get("RAILWAY_STATIC_URL")
        or ""
    ).strip()
    if not raw_value:
        return None

    base_url = raw_value if "://" in raw_value else f"https://{raw_value}"
    return f"{base_url.rstrip('/')}/telegram/webhook"


async def reconcile_telegram_webhook(settings: Settings) -> str:
    """Ensure Telegram sends updates to the current public Gate endpoint."""

    webhook_url = build_public_webhook_url(os.environ)
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not settings.telegram_bot_token:
        return "skipped_missing_bot_token"
    if not webhook_secret:
        return "skipped_missing_webhook_secret"
    if webhook_url is None:
        return "skipped_missing_public_domain"

    api_base = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/")
    bot_api_base = f"{api_base}/bot{settings.telegram_bot_token}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        info_response = await client.post(f"{bot_api_base}/getWebhookInfo", json={})
        info_response.raise_for_status()
        info_payload = _require_telegram_ok(info_response.json())
        result = info_payload.get("result")
        current_url = result.get("url") if isinstance(result, dict) else None
        last_error = result.get("last_error_message") if isinstance(result, dict) else None

        if current_url == webhook_url and not last_error:
            return "already_current"

        set_response = await client.post(
            f"{bot_api_base}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": webhook_secret,
                "drop_pending_updates": False,
            },
        )
        set_response.raise_for_status()
        _require_telegram_ok(set_response.json())
        return "updated"


async def reconcile_telegram_webhook_with_retries(settings: Settings) -> None:
    """Retry webhook reconciliation without preventing the application from starting."""

    for attempt, delay_seconds in enumerate((0, 3, 10), start=1):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            result = await reconcile_telegram_webhook(settings)
        except Exception as exc:
            logger.warning(
                "Telegram webhook reconciliation failed on attempt %s: %s",
                attempt,
                exc.__class__.__name__,
            )
            continue

        logger.info("Telegram webhook reconciliation result: %s", result)
        return


def _require_telegram_ok(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Telegram API returned a non-object response")
    normalized = cast(dict[str, Any], payload)
    if normalized.get("ok") is not True:
        raise RuntimeError("Telegram API rejected webhook request")
    return normalized
