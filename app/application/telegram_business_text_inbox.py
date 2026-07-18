"""Canonical Telegram Business text projection into the human inbox read model."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, cast

from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.telegram_canonical_projection import TelegramCanonicalProjection
from app.application.telegram_ingress_identity import (
    TelegramIngressSenderKind,
    build_telegram_business_identity_command,
)
from app.application.telegram_ingress_shadow import classify_telegram_ingress


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextInboxCommand:
    """Private canonical facts needed by the current human inbox projection."""

    account_id: str = field(repr=False)
    project_id: str = field(repr=False)
    canonical_message_id: str = field(repr=False)
    bot_username: str
    update_id: int
    event_type: str
    direction: CanonicalMessageDirection
    route: WorkRoute
    occurred_at: datetime
    provider_dialog_id: str = field(repr=False)
    provider_message_id: str = field(repr=False)
    provider_sender_id: str = field(repr=False)
    business_connection_id: str = field(repr=False)
    text_content: str = field(repr=False)
    counterparty_user_id: str = field(repr=False)
    counterparty_display_name: str | None = field(default=None, repr=False)
    counterparty_username: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.account_id, "account_id"),
            (self.project_id, "project_id"),
            (self.canonical_message_id, "canonical_message_id"),
        ):
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be a UUID") from exc
        if not self.bot_username.strip() or len(self.bot_username) > 255:
            raise ValueError("bot_username must be a bounded token")
        if self.update_id < 0:
            raise ValueError("update_id must be non-negative")
        if self.event_type not in {"business_message", "message"}:
            raise ValueError("event_type is unsupported")
        if self.direction not in {
            CanonicalMessageDirection.INBOUND,
            CanonicalMessageDirection.OUTBOUND,
        }:
            raise ValueError("direction is unsupported")
        if self.route is WorkRoute.HUMAN_REPLY_TASK and (
            self.direction is not CanonicalMessageDirection.INBOUND
        ):
            raise ValueError("human reply task requires inbound direction")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        for value, name, maximum in (
            (self.provider_dialog_id, "provider_dialog_id", 255),
            (self.provider_message_id, "provider_message_id", 255),
            (self.provider_sender_id, "provider_sender_id", 255),
            (self.business_connection_id, "business_connection_id", 255),
            (self.counterparty_user_id, "counterparty_user_id", 255),
        ):
            if not value or len(value) > maximum:
                raise ValueError(f"{name} must be bounded")
        if not self.provider_message_id.isdecimal():
            raise ValueError("provider_message_id must be numeric")
        if not self.text_content.strip() or len(self.text_content) > 100_000:
            raise ValueError("text_content must be bounded")


@dataclass(frozen=True, slots=True)
class TelegramBusinessTextInboxResult:
    """Content-free idempotent projection result."""

    duplicate: bool
    message_created: bool
    reply_task_created: bool


class TelegramBusinessTextInboxProjector(Protocol):
    """Persist exactly one current-inbox projection for one canonical message."""

    async def project(
        self,
        command: TelegramBusinessTextInboxCommand,
    ) -> TelegramBusinessTextInboxResult:
        """Project one canonical message without external delivery."""


def is_telegram_business_text_cutover_update(
    update: Mapping[str, Any],
) -> bool:
    """Select only proven ordinary human Business text for exclusive cutover."""

    mutable = dict(update)
    decision = classify_telegram_ingress(mutable)
    if decision.reason != "business_update" or decision.command is not None:
        return False
    identity = build_telegram_business_identity_command(update)
    if (
        identity is None
        or identity.sender_kind is not TelegramIngressSenderKind.HUMAN
        or not identity.text_present
    ):
        return False
    _, message = _single_message(update)
    text_content = message.get("text")
    normalized_text = text_content.strip() if isinstance(text_content, str) else ""
    return bool(normalized_text) and normalized_text.casefold() != "ping" and "voice" not in message


def build_telegram_business_text_inbox_command(
    update: Mapping[str, Any],
    *,
    account_id: str,
    project_id: str,
    canonical_message_id: str,
    bot_username: str,
    update_id: int,
    business_connection_id: str,
    projection: TelegramCanonicalProjection,
) -> TelegramBusinessTextInboxCommand:
    """Build the read-model command from the already-authoritative projection."""

    event_type, message = _single_message(update)
    direction = projection.message.direction
    raw_counterparty = message.get(
        "from" if direction is CanonicalMessageDirection.INBOUND else "chat"
    )
    counterparty = (
        cast(Mapping[str, Any], raw_counterparty) if isinstance(raw_counterparty, dict) else {}
    )
    counterparty_user_id = _provider_id(counterparty.get("id"))
    display_name = _profile_name(counterparty)
    username = _optional_text(counterparty.get("username"))
    sender_id = projection.message.provider_sender_id
    text_content = projection.message.text_content
    occurred_at = projection.message.occurred_at
    if (
        counterparty_user_id is None
        or sender_id is None
        or text_content is None
        or occurred_at is None
    ):
        raise ValueError("canonical Business text inbox identity is incomplete")
    return TelegramBusinessTextInboxCommand(
        account_id=account_id,
        project_id=project_id,
        canonical_message_id=canonical_message_id,
        bot_username=bot_username,
        update_id=update_id,
        event_type=event_type,
        direction=direction,
        route=projection.ownership.route,
        occurred_at=occurred_at,
        provider_dialog_id=projection.dialog.provider_dialog_id,
        provider_message_id=projection.message.provider_message_id,
        provider_sender_id=sender_id,
        business_connection_id=business_connection_id,
        text_content=text_content,
        counterparty_user_id=counterparty_user_id,
        counterparty_display_name=display_name,
        counterparty_username=username,
    )


def _single_message(
    update: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    business = update.get("business_message")
    legacy = update.get("message")
    has_business = isinstance(business, dict)
    has_legacy = isinstance(legacy, dict)
    if has_business == has_legacy:
        raise ValueError("exactly one Business message is required")
    if has_business:
        return "business_message", cast(Mapping[str, Any], business)
    return "message", cast(Mapping[str, Any], legacy)


def _profile_name(profile: Mapping[str, Any]) -> str | None:
    first_name = _optional_text(profile.get("first_name"))
    last_name = _optional_text(profile.get("last_name"))
    title = _optional_text(profile.get("title"))
    return " ".join(part for part in (first_name, last_name) if part) or title


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _provider_id(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value != 0:
        return str(value)
    return None
