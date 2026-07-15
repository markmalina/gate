"""Workspace-scoped Alpha installation lifecycle and usage contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.alpha_group import AlphaInstallationState
from app.application.canonical_messaging import DialogKind


class AlphaLifecycleError(RuntimeError):
    """A fail-closed lifecycle or scope violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AlphaAccessReason(StrEnum):
    ALLOWED = "allowed"
    UNKNOWN_INSTALLATION = "unknown_installation"
    CONNECTOR_DISABLED = "connector_disabled"
    INSTALLATION_INACTIVE = "installation_inactive"
    DIALOG_NOT_GROUP = "dialog_not_group"
    RATE_LIMITED = "rate_limited"
    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    TRIGGER_OUT_OF_SCOPE = "trigger_out_of_scope"


@dataclass(frozen=True, slots=True)
class DiscoverAlphaInstallationCommand:
    """A verified ``my_chat_member`` discovery fact from the Alpha connector."""

    workspace_key: str
    connector_key: str
    provider_dialog_id: str = field(repr=False)
    dialog_kind: DialogKind
    dialog_label: str
    bot_is_member: bool

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.connector_key, "connector_key", 128)
        _require_token(self.provider_dialog_id, "provider_dialog_id", 255)
        _require_text(self.dialog_label, "dialog_label", 255)
        if self.dialog_kind not in {DialogKind.GROUP, DialogKind.SUPERGROUP}:
            raise ValueError("Alpha discovery requires a group or supergroup")
        if not self.bot_is_member:
            raise ValueError("Alpha discovery requires current bot membership")


@dataclass(frozen=True, slots=True)
class ApproveAlphaInstallationCommand:
    workspace_key: str
    installation_key: str
    deployment_key: str
    revision_key: str
    max_turns_per_window: int = 5
    rate_window_seconds: int = 60
    max_tokens_per_turn: int = 2048

    def __post_init__(self) -> None:
        for name, value in (
            ("workspace_key", self.workspace_key),
            ("installation_key", self.installation_key),
            ("deployment_key", self.deployment_key),
            ("revision_key", self.revision_key),
        ):
            _require_token(value, name, 128)
        if self.max_turns_per_window < 1:
            raise ValueError("max_turns_per_window must be positive")
        if not 1 <= self.rate_window_seconds <= 86_400:
            raise ValueError("rate_window_seconds must be between 1 and 86400")
        if self.max_tokens_per_turn < 1:
            raise ValueError("max_tokens_per_turn must be positive")


@dataclass(frozen=True, slots=True)
class AlphaInstallationActionCommand:
    workspace_key: str
    installation_key: str

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.installation_key, "installation_key", 128)


@dataclass(frozen=True, slots=True)
class ReserveAlphaUsageCommand:
    workspace_key: str
    installation_key: str
    trigger_message_id: str = field(repr=False)
    reserved_tokens: int
    now: datetime

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.installation_key, "installation_key", 128)
        _require_token(self.trigger_message_id, "trigger_message_id", 128)
        if self.reserved_tokens < 1:
            raise ValueError("reserved_tokens must be positive")
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RecordAlphaUsageCommand:
    workspace_key: str
    usage_key: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.usage_key, "usage_key", 128)
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token usage cannot be negative")


@dataclass(frozen=True, slots=True)
class AlphaInstallationView:
    installation_key: str
    workspace_key: str
    connector_key: str
    connector_enabled: bool
    dialog_kind: DialogKind
    dialog_label: str
    state: AlphaInstallationState
    deployment_key: str | None
    revision_key: str | None
    max_turns_per_window: int
    rate_window_seconds: int
    max_tokens_per_turn: int
    reserved_turns: int
    recorded_tokens: int


@dataclass(frozen=True, slots=True)
class AlphaUsageReservation:
    allowed: bool
    reason: AlphaAccessReason
    usage_key: str | None = field(default=None, repr=False)
    idempotent_replay: bool = False


class AlphaLifecyclePort(Protocol):
    async def discover(
        self, command: DiscoverAlphaInstallationCommand
    ) -> AlphaInstallationView: ...

    async def approve(
        self, command: ApproveAlphaInstallationCommand
    ) -> AlphaInstallationView: ...

    async def pause(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView: ...

    async def revoke(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView: ...

    async def list_status(self, workspace_key: str) -> tuple[AlphaInstallationView, ...]: ...

    async def reserve_usage(
        self, command: ReserveAlphaUsageCommand
    ) -> AlphaUsageReservation: ...

    async def record_usage(self, command: RecordAlphaUsageCommand) -> None: ...


class AlphaLifecycleService:
    """Application boundary shared by the future worker and owner control plane."""

    def __init__(self, port: AlphaLifecyclePort) -> None:
        self._port = port

    async def discover(
        self, command: DiscoverAlphaInstallationCommand
    ) -> AlphaInstallationView:
        return await self._port.discover(command)

    async def approve(
        self, command: ApproveAlphaInstallationCommand
    ) -> AlphaInstallationView:
        return await self._port.approve(command)

    async def pause(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView:
        return await self._port.pause(command)

    async def revoke(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView:
        return await self._port.revoke(command)

    async def list_status(self, workspace_key: str) -> tuple[AlphaInstallationView, ...]:
        _require_token(workspace_key, "workspace_key", 128)
        return await self._port.list_status(workspace_key)

    async def reserve_usage(
        self, command: ReserveAlphaUsageCommand
    ) -> AlphaUsageReservation:
        return await self._port.reserve_usage(command)

    async def record_usage(self, command: RecordAlphaUsageCommand) -> None:
        await self._port.record_usage(command)


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded token")


def _require_text(value: str, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be bounded non-empty text")
