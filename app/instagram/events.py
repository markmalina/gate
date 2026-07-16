"""Normalized Instagram events emitted by Gate to ecosystem consumers."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class InstagramEventType(StrEnum):
    REEL_PUBLISHED = "instagram.reel.published.v1"
    INSIGHTS_CAPTURED = "instagram.reel.insights_captured.v1"
    COMMENT_RECEIVED = "instagram.comment.received.v1"
    MESSAGE_RECEIVED = "instagram.message.received.v1"


@dataclass(frozen=True, slots=True)
class InstagramEvent:
    event_type: InstagramEventType
    workspace_key: str
    account_alias: str
    provider_event_id: str
    occurred_at: datetime
    payload: dict[str, Any]

    @property
    def deduplication_key(self) -> str:
        source = (
            f"{self.event_type}:{self.workspace_key}:{self.account_alias}:{self.provider_event_id}"
        )
        return hashlib.sha256(source.encode()).hexdigest()

    def mayaki_envelope(self) -> dict[str, Any]:
        """Return a provider-neutral envelope; Gate never writes MAYAKI tables."""

        return {
            **asdict(self),
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "deduplication_key": self.deduplication_key,
            "source": "gate",
        }

    def mayaki_gate_intake_payload(self) -> dict[str, Any]:
        """Adapt the event to MAYAKI's authenticated Gate intake v0."""

        provider_user_id = _provider_user_id(self.event_type, self.payload)
        message_text = _message_text(self.payload)
        if not provider_user_id:
            raise ValueError("instagram_provider_user_id_required")
        if not message_text:
            raise ValueError("instagram_message_text_required")
        return {
            "gate_event_id": self.provider_event_id,
            "provider": "instagram",
            "provider_user_id": provider_user_id,
            "channel": self.event_type.value,
            "message_text": message_text,
            "occurred_at": self.occurred_at.isoformat(),
            "profile": {
                "username": _profile_username(self.payload),
                "preferred_language": "ru",
            },
            "raw": {
                "event_type": self.event_type.value,
                "workspace_key": self.workspace_key,
                "account_alias": self.account_alias,
                "deduplication_key": self.deduplication_key,
                "payload": self.payload,
            },
        }


def _provider_user_id(event_type: InstagramEventType, payload: dict[str, Any]) -> str | None:
    key = "from" if event_type is InstagramEventType.COMMENT_RECEIVED else "sender"
    identity = payload.get(key)
    if not isinstance(identity, dict):
        return None
    value = identity.get("id")
    return str(value) if value else None


def _message_text(payload: dict[str, Any]) -> str | None:
    direct = payload.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("text")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _profile_username(payload: dict[str, Any]) -> str | None:
    identity = payload.get("from")
    if not isinstance(identity, dict):
        return None
    value = identity.get("username")
    return value if isinstance(value, str) and value else None
