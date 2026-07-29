"""Reconcile the isolated Beta bot webhook with its closed update allowlist."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)
BETA_ALLOWED_UPDATES = ["message", "my_chat_member"]


def build_beta_public_webhook_url(environ: Mapping[str, str]) -> str | None:
    raw_value = (
        environ.get("PUBLIC_BASE_URL")
        or environ.get("RAILWAY_PUBLIC_DOMAIN")
        or environ.get("RAILWAY_STATIC_URL")
        or ""
    ).strip()
    if not raw_value:
        return None
    base_url = raw_value if "://" in raw_value else f"https://{raw_value}"
    return f"{base_url.rstrip('/')}/telegram/beta/webhook"


async def reconcile_beta_telegram_webhook(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> str:
    """Configure only the dedicated Alpha credential, endpoint and update types."""

    if not settings.gate_beta_connector_enabled:
        return "skipped_connector_disabled"
    token = settings.telegram_beta_bot_token.get_secret_value()
    secret = settings.telegram_beta_webhook_secret.get_secret_value()
    webhook_url = build_beta_public_webhook_url(os.environ)
    if not token:
        return "skipped_missing_bot_token"
    if not secret:
        return "skipped_missing_webhook_secret"
    if webhook_url is None:
        return "skipped_missing_public_domain"

    api_base = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/")
    bot_api_base = f"{api_base}/bot{token}"
    info_response = await http_client.post(f"{bot_api_base}/getWebhookInfo", json={})
    info_response.raise_for_status()
    result = _telegram_result(info_response.json())
    current_allowed = result.get("allowed_updates")
    allowed = (
        sorted(item for item in current_allowed if isinstance(item, str))
        if isinstance(current_allowed, list)
        else None
    )
    if (
        result.get("url") == webhook_url
        and not result.get("last_error_message")
        and allowed == sorted(BETA_ALLOWED_UPDATES)
    ):
        return "already_current"

    response = await http_client.post(
        f"{bot_api_base}/setWebhook",
        json={
            "url": webhook_url,
            "secret_token": secret,
            "drop_pending_updates": False,
            "allowed_updates": BETA_ALLOWED_UPDATES,
        },
    )
    response.raise_for_status()
    _telegram_result(response.json(), require_result=False)
    return "updated"


async def reconcile_beta_telegram_webhook_with_retries(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> None:
    for attempt, delay_seconds in enumerate((0, 3, 10), start=1):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            result = await reconcile_beta_telegram_webhook(settings, http_client)
        except Exception as exc:
            logger.warning(
                "Beta Telegram webhook reconciliation failed on attempt %s: %s",
                attempt,
                exc.__class__.__name__,
            )
            continue
        logger.info("Beta Telegram webhook reconciliation result: %s", result)
        return


def _telegram_result(payload: Any, *, require_result: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Telegram API rejected Beta webhook request")
    result = cast(dict[str, Any], payload).get("result")
    if require_result and not isinstance(result, dict):
        raise RuntimeError("Telegram API returned invalid Beta webhook state")
    return cast(dict[str, Any], result) if isinstance(result, dict) else {}
