"""Reconcile the isolated BECOME bot webhook with its closed update allowlist."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)
BECOME_ALLOWED_UPDATES = ["message", "my_chat_member", "callback_query"]


def build_become_public_webhook_url(environ: Mapping[str, str]) -> str | None:
    raw_value = (
        environ.get("PUBLIC_BASE_URL")
        or environ.get("RAILWAY_PUBLIC_DOMAIN")
        or environ.get("RAILWAY_STATIC_URL")
        or ""
    ).strip()
    if not raw_value:
        return None
    base_url = raw_value if "://" in raw_value else f"https://{raw_value}"
    return f"{base_url.rstrip('/')}/telegram/become/webhook"


async def reconcile_become_telegram_webhook(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> str:
    """Configure only the dedicated BECOME credential, endpoint and update types."""

    if not settings.gate_become_connector_enabled:
        return "skipped_connector_disabled"
    token = settings.telegram_become_bot_token.get_secret_value().strip()
    secret = settings.telegram_become_webhook_secret.get_secret_value().strip()
    webhook_url = build_become_public_webhook_url(os.environ)
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
        and allowed == sorted(BECOME_ALLOWED_UPDATES)
    ):
        return "already_current"

    response = await http_client.post(
        f"{bot_api_base}/setWebhook",
        json={
            "url": webhook_url,
            "secret_token": secret,
            "drop_pending_updates": False,
            "allowed_updates": BECOME_ALLOWED_UPDATES,
        },
    )
    response.raise_for_status()
    _telegram_result(response.json(), require_result=False)
    return "updated"


async def reconcile_become_telegram_webhook_with_retries(
    settings: Settings,
    http_client: httpx.AsyncClient,
    *,
    retry_delays: tuple[int, ...] = (0, 3, 10, 60, 300),
    healthy_interval_seconds: int = 300,
) -> None:
    """Keep the one global BECOME webhook current until application shutdown."""

    if not retry_delays or retry_delays[0] != 0:
        raise ValueError("retry_delays must start with zero")
    if healthy_interval_seconds < 1:
        raise ValueError("healthy_interval_seconds must be positive")

    failed_attempts = 0
    while True:
        delay_seconds = retry_delays[min(failed_attempts, len(retry_delays) - 1)]
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            result = await reconcile_become_telegram_webhook(settings, http_client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_attempts += 1
            reason, status_code, operation = become_webhook_failure_details(exc)
            logger.warning(
                "BECOME Telegram webhook reconciliation failed: "
                "attempt=%s operation=%s reason=%s status_code=%s",
                failed_attempts,
                operation,
                reason,
                status_code if status_code is not None else "none",
            )
            continue
        logger.info("BECOME Telegram webhook reconciliation result: %s", result)
        failed_attempts = 0
        await asyncio.sleep(healthy_interval_seconds)


def become_webhook_failure_details(
    exc: Exception,
) -> tuple[str, int | None, str]:
    """Return redacted diagnostics without credentials, hosts or response bodies."""

    operation = _telegram_operation(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "http_authentication_failed", status_code, operation
        if status_code == 400:
            return _become_bad_request_reason(exc.response, operation), 400, operation
        if status_code == 429:
            return "http_rate_limited", status_code, operation
        if status_code >= 500:
            return "http_server_error", status_code, operation
        return "http_client_error", status_code, operation
    if isinstance(exc, httpx.TimeoutException):
        return "transport_timeout", None, operation
    if isinstance(exc, httpx.RequestError):
        return "transport_error", None, operation
    if isinstance(exc, RuntimeError):
        return "telegram_rejected_or_invalid_response", None, operation
    return "unexpected_error", None, operation


def _telegram_operation(exc: Exception) -> str:
    if not isinstance(exc, httpx.HTTPError) or exc.request is None:
        return "unknown"
    path = exc.request.url.path
    if path.endswith("/getWebhookInfo"):
        return "get_webhook_info"
    if path.endswith("/setWebhook"):
        return "set_webhook"
    return "telegram_api"


def _become_bad_request_reason(
    response: httpx.Response,
    operation: str,
) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    description = (
        payload.get("description", "").lower()
        if isinstance(payload, dict)
        and isinstance(payload.get("description"), str)
        else ""
    )
    if operation == "set_webhook":
        if "secret token" in description:
            return "webhook_secret_invalid"
        if any(
            marker in description
            for marker in ("bad webhook", "webhook url", "https url", "resolve host")
        ):
            return "webhook_url_invalid"
        if "allowed_updates" in description:
            return "allowed_updates_invalid"
        return "set_webhook_rejected"
    if operation == "get_webhook_info":
        return "get_webhook_info_rejected"
    return "http_bad_request"


def _telegram_result(payload: Any, *, require_result: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Telegram API rejected BECOME webhook request")
    result = cast(dict[str, Any], payload).get("result")
    if require_result and not isinstance(result, dict):
        raise RuntimeError("Telegram API returned invalid BECOME webhook state")
    return cast(dict[str, Any], result) if isinstance(result, dict) else {}
