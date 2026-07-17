"""Authenticated MAYAKI to Gate command admission and result mapping."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.instagram.commands import InstagramProviderCommand
from app.instagram.meta import MetaInstagramCommandExecutor, MetaInstagramError

COMMAND_PATH = "/internal/instagram/commands"


class InstagramCommandGatewayError(ValueError):
    """A safe command admission error."""


def sign_instagram_command(
    *, timestamp: str, idempotency_key: str, raw_body: str, secret: str
) -> str:
    canonical = f"POST\n{COMMAND_PATH}\n{timestamp}\n{idempotency_key}\n{raw_body}"
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify_instagram_command(
    *,
    raw_body: str,
    authorization: str,
    timestamp: str,
    signature: str,
    idempotency_key: str,
    bearer_token: str,
    hmac_secret: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_clock_skew_seconds: int = 300,
) -> dict[str, Any]:
    if not bearer_token or not hmac_secret:
        raise InstagramCommandGatewayError("instagram_command_bridge_not_configured")
    if not hmac.compare_digest(authorization, f"Bearer {bearer_token}"):
        raise InstagramCommandGatewayError("instagram_command_unauthorized")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        decoded = json.loads(raw_body)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InstagramCommandGatewayError("instagram_command_invalid") from exc
    if not isinstance(decoded, dict):
        raise InstagramCommandGatewayError("instagram_command_invalid")
    payload: dict[str, Any] = decoded
    if parsed_timestamp.tzinfo is None:
        parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
    if abs((now() - parsed_timestamp.astimezone(UTC)).total_seconds()) > max_clock_skew_seconds:
        raise InstagramCommandGatewayError("instagram_command_timestamp_expired")
    expected = sign_instagram_command(
        timestamp=timestamp,
        idempotency_key=idempotency_key,
        raw_body=raw_body,
        secret=hmac_secret,
    )
    if not hmac.compare_digest(signature, expected):
        raise InstagramCommandGatewayError("instagram_command_signature_invalid")
    command = InstagramProviderCommand.from_mapping(payload)
    if not hmac.compare_digest(command.idempotency_key, idempotency_key):
        raise InstagramCommandGatewayError("instagram_command_idempotency_mismatch")
    return payload


async def execute_authenticated_command(
    *,
    executor: MetaInstagramCommandExecutor,
    raw_body: str,
    authorization: str,
    timestamp: str,
    signature: str,
    idempotency_key: str,
    bearer_token: str,
    hmac_secret: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    command = verify_instagram_command(
        raw_body=raw_body,
        authorization=authorization,
        timestamp=timestamp,
        signature=signature,
        idempotency_key=idempotency_key,
        bearer_token=bearer_token,
        hmac_secret=hmac_secret,
        now=now,
    )
    try:
        return await executor.execute(command)
    except MetaInstagramError as exc:
        result: dict[str, Any] = {
            "status": "unknown" if exc.code == "unknown" else "failed",
            "error": exc.code,
            "message": str(exc),
            "idempotency_key": idempotency_key,
            "external_delivery": False,
            "retryable": False,
        }
        if exc.code == "unknown" and executor.result_store is not None:
            executor.result_store.put(idempotency_key, result)
        return result
