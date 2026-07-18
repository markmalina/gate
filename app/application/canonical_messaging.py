"""Connector-neutral contracts for canonical messaging and work ownership.

The contracts in this module are deliberately persistence- and transport-free.  They
establish the workspace/dialog/message identity rules that every future ingress and
agent adapter must obey before any authoritative cutover is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.work_ownership import AgentKey, WorkOwnershipState


class ConnectorKind(StrEnum):
    """Transport capabilities, kept separate from a credential or provider id."""

    TELEGRAM_BUSINESS = "telegram_business"
    TELEGRAM_BOT = "telegram_bot"


class DialogKind(StrEnum):
    """Connector-neutral dialog shapes supported by the release program."""

    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class CanonicalMessageDirection(StrEnum):
    """Fail-closed direction evidence stored with an immutable message."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOT_OR_SERVICE = "bot_or_service"
    UNKNOWN = "unknown"


class WorkRoute(StrEnum):
    """Exactly one durable downstream outcome selected after ownership."""

    HUMAN_REPLY_TASK = "human_reply_task"
    AGENT_TURN = "agent_turn"
    AGENT_HANDOFF = "agent_handoff"
    IGNORED = "ignored"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    """Stable tenant boundary required on every new aggregate root."""

    workspace_id: str

    def __post_init__(self) -> None:
        _require_token(self.workspace_id, field_name="workspace_id", maximum=128)


@dataclass(frozen=True, slots=True)
class DialogIdentity:
    """A dialog identity independent of project assignment and raw message text."""

    workspace_id: str
    connector_key: str
    connector_kind: ConnectorKind
    provider_dialog_id: str = field(repr=False)
    dialog_kind: DialogKind = DialogKind.PRIVATE

    def __post_init__(self) -> None:
        _require_token(self.workspace_id, field_name="workspace_id", maximum=128)
        _require_token(self.connector_key, field_name="connector_key", maximum=128)
        _require_token(
            self.provider_dialog_id,
            field_name="provider_dialog_id",
            maximum=255,
        )


@dataclass(frozen=True, slots=True)
class CanonicalMessageEnvelope:
    """Immutable normalized fact accepted by the ownership boundary."""

    workspace_id: str
    connector_key: str
    source_receipt_id: str = field(repr=False)
    provider_dialog_id: str = field(repr=False)
    provider_message_id: str = field(repr=False)
    provider_sender_id: str | None = field(default=None, repr=False)
    direction: CanonicalMessageDirection = CanonicalMessageDirection.UNKNOWN
    occurred_at: datetime | None = None
    text_present: bool = False
    text_content: str | None = field(default=None, repr=False)
    input_origin: str = "telegram_text"

    def __post_init__(self) -> None:
        _require_token(self.workspace_id, field_name="workspace_id", maximum=128)
        _require_token(self.connector_key, field_name="connector_key", maximum=128)
        _require_token(
            self.source_receipt_id,
            field_name="source_receipt_id",
            maximum=128,
        )
        _require_token(
            self.provider_dialog_id,
            field_name="provider_dialog_id",
            maximum=255,
        )
        _require_token(
            self.provider_message_id,
            field_name="provider_message_id",
            maximum=255,
        )
        if self.input_origin not in {
            "telegram_text",
            "telegram_voice_transcription",
        }:
            raise ValueError("input_origin is unsupported")
        content_present = (
            isinstance(self.text_content, str) and bool(self.text_content.strip())
        )
        if content_present != self.text_present:
            raise ValueError("text_content and text_present must agree")
        if self.text_content is not None and len(self.text_content) > 100_000:
            raise ValueError("text_content is too large")


@dataclass(frozen=True, slots=True)
class WorkOwnershipDecision:
    """Versioned result that cannot grant agent work without an explicit route."""

    state: WorkOwnershipState
    route: WorkRoute
    policy_version: str
    candidate_agent: AgentKey | None = None
    reason_code: str = "unspecified"

    def __post_init__(self) -> None:
        _require_token(self.policy_version, field_name="policy_version", maximum=128)
        _require_token(self.reason_code, field_name="reason_code", maximum=128)
        if self.route is WorkRoute.AGENT_TURN and (
            self.state is not WorkOwnershipState.AGENT_MANAGED
            or self.candidate_agent is None
        ):
            raise ValueError("agent_turn requires agent_managed state and an agent")
        if self.route is not WorkRoute.AGENT_TURN and (
            self.state is WorkOwnershipState.AGENT_MANAGED
        ):
            raise ValueError("agent_managed state requires the agent_turn route")
        if self.route is WorkRoute.HUMAN_REPLY_TASK and self.state not in {
            WorkOwnershipState.HUMAN_MANAGED,
            WorkOwnershipState.HANDOFF_REQUIRED,
        }:
            raise ValueError("human reply work requires human or handoff ownership")


@dataclass(frozen=True, slots=True)
class CanonicalIngressResult:
    """Opaque durable identities returned after one atomic persistence operation."""

    dialog_id: str
    message_id: str
    ownership_id: str
    duplicate: bool


class CanonicalIngressStore(Protocol):
    """Atomic persistence boundary; implementations must not call external services."""

    async def persist(
        self,
        *,
        dialog: DialogIdentity,
        message: CanonicalMessageEnvelope,
        ownership: WorkOwnershipDecision,
    ) -> CanonicalIngressResult:
        """Persist one idempotent dialog/message/ownership decision."""


def _require_token(value: str, *, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded non-empty token")
