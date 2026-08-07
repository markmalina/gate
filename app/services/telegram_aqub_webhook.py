"""Default-dormant provider preflight for the isolated AQUB Telegram bot."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.application.aqub_s0_preflight import AQUB_BOT_USERNAME, AQUB_OWNED_PATH
from app.core.config import Settings
from app.infrastructure.aqub_s0_preflight import build_aqub_s0_binding

logger = logging.getLogger(__name__)
AQUB_ALLOWED_UPDATES = ["message", "my_chat_member"]
_TELEGRAM_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_NUMERIC_HOST_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)")


class AQUBProviderErrorCode(StrEnum):
    CONFIGURATION_INCOMPLETE = "configuration_incomplete"
    PROVIDER_REQUEST_FAILED = "provider_request_failed"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_RESPONSE_INVALID = "provider_response_invalid"
    IDENTITY_MISMATCH = "identity_mismatch"
    GROUP_JOIN_DISABLED = "group_join_disabled"
    PRIVACY_MODE_DISABLED = "privacy_mode_disabled"
    WEBHOOK_REJECTED = "webhook_rejected"


class AQUBProviderPreflightError(RuntimeError):
    """One stable provider error that contains no provider response data."""

    def __init__(self, code: AQUBProviderErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class AQUBProviderReadinessSnapshot:
    enabled: bool = False
    configured: bool = False
    identity_verified: bool = False
    can_join_groups: bool = False
    privacy_mode_enabled: bool = False
    webhook_configured: bool = False
    ready: bool = False
    error_code: AQUBProviderErrorCode | None = None


class AQUBProviderReadinessState:
    """Process-local, secret-free AQUB provider readiness observation."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._snapshot = AQUBProviderReadinessSnapshot(enabled=enabled)

    def snapshot(self) -> AQUBProviderReadinessSnapshot:
        return self._snapshot

    def set(self, snapshot: AQUBProviderReadinessSnapshot) -> None:
        self._snapshot = snapshot

    def fail(self, code: AQUBProviderErrorCode) -> None:
        self._snapshot = replace(self._snapshot, ready=False, error_code=code)


def build_aqub_public_webhook_url(environ: Mapping[str, str]) -> str | None:
    raw_value = (
        environ.get("PUBLIC_BASE_URL")
        or environ.get("RAILWAY_PUBLIC_DOMAIN")
        or environ.get("RAILWAY_STATIC_URL")
        or ""
    )
    if not raw_value or _contains_ascii_control_or_space(raw_value):
        return None
    if "://" not in raw_value:
        if any(marker in raw_value for marker in ("/", "?", "#", "@")):
            return None
        raw_value = f"https://{raw_value}"
    try:
        parsed = urlsplit(raw_value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not _valid_explicit_port(parsed.netloc)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or not _valid_public_host(parsed.hostname)
    ):
        return None
    return f"https://{parsed.netloc}{AQUB_OWNED_PATH}"


def _valid_public_host(hostname: str | None) -> bool:
    if hostname is None or hostname != hostname.strip() or len(hostname) > 253:
        return False
    canonical = hostname.casefold()
    if canonical.endswith(".") or "%" in canonical:
        return False
    try:
        address = ipaddress.ip_address(canonical)
        if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
            return False
        return _is_global_unicast(address)
    except ValueError:
        pass
    labels = canonical.split(".")
    if labels and all(_NUMERIC_HOST_LABEL.fullmatch(label) for label in labels):
        return False
    if (
        "." not in canonical
        or canonical in {"localhost", "local", "localdomain"}
        or canonical.endswith((".localhost", ".local", ".localdomain"))
    ):
        return False
    return bool(labels) and all(_DNS_LABEL.fullmatch(label) for label in labels)


def _contains_ascii_control_or_space(value: str) -> bool:
    return any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)


def _is_global_unicast(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return address.is_global and not (
        address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
        or address.is_private
    )


def _valid_explicit_port(netloc: str) -> bool:
    """Reject an authority port delimiter unless decimal digits follow it."""

    if netloc.startswith("["):
        bracket = netloc.find("]")
        if bracket < 0:
            return False
        suffix = netloc[bracket + 1 :]
        return not suffix or bool(re.fullmatch(r":[0-9]+", suffix))
    if ":" not in netloc:
        return True
    host, _, port_text = netloc.rpartition(":")
    return bool(host) and bool(re.fullmatch(r"[0-9]+", port_text))


async def reconcile_aqub_telegram_provider(
    settings: Settings,
    http_client: httpx.AsyncClient,
    state: AQUBProviderReadinessState,
) -> str:
    """Verify the pinned bot and Privacy Mode before setting its webhook."""

    enabled = settings.gate_aqub_s0_preflight_enabled
    state.set(AQUBProviderReadinessSnapshot(enabled=enabled))
    if not enabled:
        return "skipped_preflight_disabled"

    try:
        binding = build_aqub_s0_binding(settings)
    except ValueError:
        state.fail(AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE)
        return "skipped_configuration_incomplete"
    if not binding.enabled:
        state.fail(AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE)
        return "skipped_configuration_incomplete"

    token = settings.telegram_aqub_bot_token.get_secret_value().strip()
    secret = settings.telegram_aqub_webhook_secret.get_secret_value().strip()
    webhook_url = build_aqub_public_webhook_url(os.environ)
    if not token or not secret or webhook_url is None:
        state.fail(AQUBProviderErrorCode.CONFIGURATION_INCOMPLETE)
        return "skipped_configuration_incomplete"

    state.set(AQUBProviderReadinessSnapshot(enabled=True, configured=True))
    api_base = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/")
    bot_api_base = f"{api_base}/bot{token}"
    try:
        identity_response = await http_client.post(
            f"{bot_api_base}/getMe",
            json={},
            timeout=10.0,
        )
        identity_response.raise_for_status()
        identity = _telegram_object(identity_response.json())
    except AQUBProviderPreflightError as exc:
        state.fail(exc.code)
        raise
    except (httpx.HTTPError, ValueError) as exc:
        state.fail(AQUBProviderErrorCode.PROVIDER_REQUEST_FAILED)
        raise AQUBProviderPreflightError(
            AQUBProviderErrorCode.PROVIDER_REQUEST_FAILED
        ) from exc

    normalized_username = _normalize_provider_username(identity.get("username"))
    if identity.get("is_bot") is not True or normalized_username != AQUB_BOT_USERNAME:
        state.fail(AQUBProviderErrorCode.IDENTITY_MISMATCH)
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.IDENTITY_MISMATCH)

    can_join_groups = identity.get("can_join_groups") is True
    privacy_mode_enabled = identity.get("can_read_all_group_messages") is False
    state.set(
        AQUBProviderReadinessSnapshot(
            enabled=True,
            configured=True,
            identity_verified=True,
            can_join_groups=can_join_groups,
            privacy_mode_enabled=privacy_mode_enabled,
        )
    )
    if not can_join_groups:
        state.fail(AQUBProviderErrorCode.GROUP_JOIN_DISABLED)
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.GROUP_JOIN_DISABLED)
    if not privacy_mode_enabled:
        state.fail(AQUBProviderErrorCode.PRIVACY_MODE_DISABLED)
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.PRIVACY_MODE_DISABLED)

    try:
        webhook_response = await http_client.post(
            f"{bot_api_base}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": secret,
                "drop_pending_updates": False,
                "allowed_updates": AQUB_ALLOWED_UPDATES,
            },
            timeout=10.0,
        )
        webhook_response.raise_for_status()
        _telegram_true(webhook_response.json())
    except AQUBProviderPreflightError as exc:
        state.fail(exc.code)
        raise
    except (httpx.HTTPError, ValueError) as exc:
        state.fail(AQUBProviderErrorCode.WEBHOOK_REJECTED)
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.WEBHOOK_REJECTED) from exc

    state.set(
        AQUBProviderReadinessSnapshot(
            enabled=True,
            configured=True,
            identity_verified=True,
            can_join_groups=True,
            privacy_mode_enabled=True,
            webhook_configured=True,
            ready=True,
        )
    )
    return "updated"


async def reconcile_aqub_telegram_provider_with_retries(
    settings: Settings,
    http_client: httpx.AsyncClient,
    state: AQUBProviderReadinessState,
    *,
    retry_delays: tuple[int, ...] = (0, 3, 10),
) -> None:
    """Retry a bounded number of times and log only stable error codes."""

    if not retry_delays or retry_delays[0] != 0:
        raise ValueError("retry_delays must start with zero")
    for attempt, delay_seconds in enumerate(retry_delays, start=1):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            result = await reconcile_aqub_telegram_provider(
                settings,
                http_client,
                state,
            )
        except asyncio.CancelledError:
            raise
        except AQUBProviderPreflightError as exc:
            logger.warning(
                "AQUB Telegram provider preflight failed: attempt=%s code=%s",
                attempt,
                exc.code.value,
            )
            continue
        logger.info("AQUB Telegram provider preflight result: %s", result)
        return


def _normalize_provider_username(value: Any) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    username = value[1:] if value.startswith("@") else value
    if _TELEGRAM_USERNAME.fullmatch(username) is None:
        return None
    return f"@{username}"


def _telegram_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.PROVIDER_REJECTED)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AQUBProviderPreflightError(
            AQUBProviderErrorCode.PROVIDER_RESPONSE_INVALID
        )
    return result


def _telegram_true(payload: Any) -> None:
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("result") is not True
    ):
        raise AQUBProviderPreflightError(AQUBProviderErrorCode.WEBHOOK_REJECTED)
