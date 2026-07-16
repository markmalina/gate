"""Verified, provider-neutral normalization for Meta Instagram webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from app.instagram.events import InstagramEvent, InstagramEventType


class InstagramWebhookError(ValueError):
    """Webhook authenticity or contract failure."""


def verify_meta_signature(*, body: bytes, signature_header: str | None, app_secret: str) -> None:
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        raise InstagramWebhookError("missing webhook signature configuration")
    supplied = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise InstagramWebhookError("invalid webhook signature")


def normalize_webhook(
    *,
    body: bytes,
    workspace_key: str,
    account_alias_by_provider_id: dict[str, str],
) -> list[InstagramEvent]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstagramWebhookError("invalid webhook JSON") from exc
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        raise InstagramWebhookError("unsupported webhook object")
    events: list[InstagramEvent] = []
    for entry in _dict_items(payload.get("entry")):
        account_id = str(entry.get("id", ""))
        account_alias = account_alias_by_provider_id.get(account_id)
        if not account_alias:
            continue
        occurred_at = datetime.fromtimestamp(_timestamp(entry.get("time")), tz=UTC)
        for change in _dict_items(entry.get("changes")):
            field = change.get("field")
            value = change.get("value")
            if field != "comments" or not isinstance(value, dict):
                continue
            provider_event_id = str(
                value.get("id") or value.get("comment_id") or _payload_hash(value)
            )
            events.append(
                InstagramEvent(
                    event_type=InstagramEventType.COMMENT_RECEIVED,
                    workspace_key=workspace_key,
                    account_alias=account_alias,
                    provider_event_id=provider_event_id,
                    occurred_at=occurred_at,
                    payload=value,
                )
            )
        for message in _dict_items(entry.get("messaging")):
            provider_event_id = str(
                _nested_id(message, "message")
                or _nested_id(message, "reaction")
                or _payload_hash(message)
            )
            events.append(
                InstagramEvent(
                    event_type=InstagramEventType.MESSAGE_RECEIVED,
                    workspace_key=workspace_key,
                    account_alias=account_alias,
                    provider_event_id=provider_event_id,
                    occurred_at=occurred_at,
                    payload=message,
                )
            )
    return events


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _nested_id(value: dict[str, Any], key: str) -> str | None:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return None
    identifier = nested.get("mid") or nested.get("id")
    return str(identifier) if identifier else None


def _payload_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _timestamp(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0
