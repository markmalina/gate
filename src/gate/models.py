"""Core Gate domain models."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ReplyMode(StrEnum):
    MANUAL = "manual"
    APPROVAL = "approval"
    AUTO_SAFE = "auto-safe"
    AUTO_FULL = "auto-full"


class AccountType(StrEnum):
    PERSONAL = "personal"
    BELARUS = "belarus"
    RUSSIAN = "russian"
    PROJECT = "project"
    CLIENT = "client"


@dataclass(frozen=True)
class Bot:
    id: str
    username: str
    token_secret_ref: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class Account:
    id: str
    owner: str
    account_type: AccountType
    linked_project_id: str | None = None
    connected_bot_ids: tuple[str, ...] = ()
    permissions: dict[str, Any] = field(default_factory=dict)
    reply_mode: ReplyMode = ReplyMode.MANUAL
    audit_log_enabled: bool = True


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    adapter_type: str
    memory_target: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class Route:
    id: str
    bot_id: str | None
    account_id: str | None
    chat_id: str | None
    event_type: str | None
    project_id: str
    reply_mode_override: ReplyMode | None = None
    priority: int = 100
    is_active: bool = True


@dataclass(frozen=True)
class GateEvent:
    event_id: str
    bot_id: str
    account_id: str | None
    chat_id: str
    sender_id: str
    event_type: str
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SuggestedReply:
    text: str
    project_id: str
    mode: ReplyMode
    metadata: dict[str, Any] = field(default_factory=dict)
