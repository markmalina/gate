"""One-call, redacted Telegram webhook diagnostic for the Alpha bot."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings

JsonObject = dict[str, Any]


class TelegramWebhookStatusError(RuntimeError):
    """A stable, non-secret error from the bounded webhook diagnostic."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def get_alpha_webhook_status(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> JsonObject:
    """Call getWebhookInfo exactly once and return only redacted status fields."""

    token = settings.telegram_alpha_bot_token.get_secret_value().strip()
    username = settings.telegram_alpha_bot_username.strip().lstrip("@")
    resource = settings.mcp_public_resource_url.strip().rstrip("/")
    if not token or not username:
        raise TelegramWebhookStatusError("alpha_webhook_credentials_incomplete")
    if not resource:
        raise TelegramWebhookStatusError("alpha_webhook_public_url_missing")

    expected_url = f"{resource.removesuffix('/mcp')}/telegram/alpha/webhook"
    endpoint = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        response = await http_client.get(endpoint, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramWebhookStatusError(
            "alpha_webhook_provider_read_failed"
        ) from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramWebhookStatusError("alpha_webhook_provider_rejected")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TelegramWebhookStatusError("alpha_webhook_provider_invalid")

    actual_url = result.get("url")
    if not isinstance(actual_url, str):
        actual_url = ""
    actual = urlsplit(actual_url)
    expected = urlsplit(expected_url)

    pending = result.get("pending_update_count")
    if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
        pending = 0
    max_connections = result.get("max_connections")
    if (
        not isinstance(max_connections, int)
        or isinstance(max_connections, bool)
        or max_connections < 1
    ):
        max_connections = None
    allowed_updates = result.get("allowed_updates")
    if not isinstance(allowed_updates, list):
        allowed_updates = []
    safe_updates = [
        value[:64]
        for value in allowed_updates[:32]
        if isinstance(value, str) and value
    ]

    return {
        "api_ok": True,
        "bot_username": f"@{username}",
        "webhook_configured": bool(actual_url),
        "exact_match": actual_url == expected_url,
        "actual_origin_match": (
            bool(actual_url)
            and actual.scheme == expected.scheme
            and actual.netloc == expected.netloc
        ),
        "expected_webhook_path": expected.path,
        "pending_update_count": pending,
        "allowed_updates": safe_updates,
        "max_connections": max_connections,
        "has_custom_certificate": bool(result.get("has_custom_certificate")),
        "last_error_present": result.get("last_error_date") is not None,
        "provider_dialog_ids_exposed": False,
        "telegram_private_data_exposed": False,
    }

async def get_beta_provider_probe(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> JsonObject:
    """Call only Beta getWebhookInfo/getMe and return a secret-safe snapshot."""

    token = settings.telegram_beta_bot_token.get_secret_value().strip()
    expected_username = settings.telegram_beta_bot_username.strip().lstrip("@")
    resource = settings.mcp_public_resource_url.strip().rstrip("/")
    if not token or not expected_username:
        raise TelegramWebhookStatusError("beta_probe_credentials_incomplete")
    if not resource:
        raise TelegramWebhookStatusError("beta_probe_public_url_missing")

    results: dict[str, JsonObject] = {}
    for method in ("getWebhookInfo", "getMe"):
        endpoint = f"https://api.telegram.org/bot{token}/{method}"
        try:
            response = await http_client.get(endpoint, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramWebhookStatusError("beta_probe_provider_read_failed") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramWebhookStatusError("beta_probe_provider_rejected")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TelegramWebhookStatusError("beta_probe_provider_invalid")
        results[method] = result

    webhook_result = results["getWebhookInfo"]
    actual_url = webhook_result.get("url")
    if not isinstance(actual_url, str):
        actual_url = ""
    actual = urlsplit(actual_url)
    expected_url = f"{resource.removesuffix('/mcp')}/telegram/beta/webhook"
    expected = urlsplit(expected_url)

    pending = webhook_result.get("pending_update_count")
    if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
        pending = 0
    allowed_updates = webhook_result.get("allowed_updates")
    if not isinstance(allowed_updates, list):
        allowed_updates = []
    safe_updates = [
        value
        for value in allowed_updates[:32]
        if isinstance(value, str)
        and value
        and len(value) <= 64
        and value.replace("_", "").isalnum()
    ]
    max_connections = webhook_result.get("max_connections")
    if (
        not isinstance(max_connections, int)
        or isinstance(max_connections, bool)
        or max_connections < 1
    ):
        max_connections = None
    last_error_date = webhook_result.get("last_error_date")
    if (
        not isinstance(last_error_date, int)
        or isinstance(last_error_date, bool)
        or last_error_date < 0
    ):
        last_error_date = None
    last_error_description = webhook_result.get("last_error_message")
    last_error_present = last_error_date is not None or (
        isinstance(last_error_description, str) and bool(last_error_description)
    )

    identity_result = results["getMe"]
    if identity_result.get("is_bot") is not True:
        raise TelegramWebhookStatusError("beta_probe_identity_invalid")
    actual_username = identity_result.get("username")
    username_match = (
        isinstance(actual_username, str)
        and actual_username.casefold() == expected_username.casefold()
    )
    if not username_match:
        raise TelegramWebhookStatusError("beta_probe_identity_mismatch")
    can_join_groups = identity_result.get("can_join_groups") is True
    can_read_all_group_messages = (
        identity_result.get("can_read_all_group_messages") is True
    )

    return {
        "provider": "telegram_bot_api",
        "provider_calls": 2,
        "call_surface": {
            "read_methods": ["getWebhookInfo", "getMe"],
            "mutations_allowed": False,
        },
        "webhook": {
            "configured": bool(actual_url),
            "exact_match": actual_url == expected_url,
            "actual_origin_match": (
                bool(actual_url)
                and actual.scheme == expected.scheme
                and actual.netloc == expected.netloc
            ),
            "url_host": actual.hostname or "",
            "url_path": actual.path,
            "expected_path": expected.path,
            "allowed_updates": safe_updates,
            "pending_update_count": pending,
            "max_connections": max_connections,
            "has_custom_certificate": bool(
                webhook_result.get("has_custom_certificate")
            ),
            "last_error": {
                "present": last_error_present,
                "date": last_error_date,
                "message": (
                    "provider_error_redacted" if last_error_present else None
                ),
            },
        },
        "identity": {
            "username_match": username_match,
            "can_join_groups": can_join_groups,
            "can_read_all_group_messages": can_read_all_group_messages,
            "privacy_mode_enabled": not can_read_all_group_messages,
            "ordinary_group_messages_available": (
                can_join_groups and can_read_all_group_messages
            ),
        },
        "provider_identifiers_exposed": False,
        "telegram_private_data_exposed": False,
        "secrets_exposed": False,
    }
