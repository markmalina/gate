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
