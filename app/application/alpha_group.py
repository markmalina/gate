"""Fail-closed Alpha group installation, invocation and delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.application.agent_shadow import AgentShadowProposal
from app.application.canonical_messaging import DialogKind
from app.application.work_ownership import AgentKey


class AlphaInstallationState(StrEnum):
    DISCOVERED = "discovered"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"


class AlphaInvocationKind(StrEnum):
    COMMAND = "command"
    MENTION = "mention"
    REPLY_TO_BOT = "reply_to_bot"
    NONE = "none"


class AlphaPolicyReason(StrEnum):
    ALLOWED = "allowed"
    CONNECTOR_DISABLED = "connector_disabled"
    INSTALLATION_INACTIVE = "installation_inactive"
    DIALOG_NOT_GROUP = "dialog_not_group"
    SENDER_NOT_HUMAN = "sender_not_human"
    NOT_INVOKED = "not_invoked"
    RATE_LIMITED = "rate_limited"


class AgentSendAttemptState(StrEnum):
    RESERVED = "reserved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class AlphaInstallation:
    """One approved deployment in exactly one workspace group dialog."""

    installation_key: str
    workspace_id: str
    dialog_id: str = field(repr=False)
    dialog_kind: DialogKind
    deployment_key: str
    instruction_content_sha256: str = field(repr=False)
    state: AlphaInstallationState = AlphaInstallationState.DISCOVERED

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("installation_key", self.installation_key, 128),
            ("workspace_id", self.workspace_id, 128),
            ("dialog_id", self.dialog_id, 128),
            ("deployment_key", self.deployment_key, 128),
        ):
            if not value or value != value.strip() or len(value) > maximum:
                raise ValueError(f"{name} must be a bounded token")
        if self.dialog_kind not in {DialogKind.GROUP, DialogKind.SUPERGROUP}:
            raise ValueError("Alpha installation requires a group or supergroup")
        if len(self.instruction_content_sha256) != 64:
            raise ValueError("instruction_content_sha256 must be a SHA-256 digest")

    def request_approval(self) -> AlphaInstallation:
        if self.state is not AlphaInstallationState.DISCOVERED:
            raise ValueError("only a discovered installation can request approval")
        return replace(self, state=AlphaInstallationState.PENDING_APPROVAL)

    def approve(self) -> AlphaInstallation:
        if self.state is not AlphaInstallationState.PENDING_APPROVAL:
            raise ValueError("only a pending installation can be approved")
        return replace(self, state=AlphaInstallationState.ACTIVE)

    def pause(self) -> AlphaInstallation:
        if self.state is not AlphaInstallationState.ACTIVE:
            raise ValueError("only an active installation can be paused")
        return replace(self, state=AlphaInstallationState.PAUSED)

    def revoke(self) -> AlphaInstallation:
        if self.state is AlphaInstallationState.REVOKED:
            return self
        return replace(self, state=AlphaInstallationState.REVOKED)


@dataclass(frozen=True, slots=True)
class AlphaInvocationFacts:
    connector_enabled: bool
    installation: AlphaInstallation
    sender_is_human: bool
    invocation_kind: AlphaInvocationKind
    turns_in_window: int
    max_turns_per_window: int


@dataclass(frozen=True, slots=True)
class AlphaInvocationDecision:
    allowed: bool
    reason: AlphaPolicyReason


def evaluate_alpha_invocation(facts: AlphaInvocationFacts) -> AlphaInvocationDecision:
    """Allow only explicit human invocation in an approved group installation."""

    if not facts.connector_enabled:
        return AlphaInvocationDecision(False, AlphaPolicyReason.CONNECTOR_DISABLED)
    if facts.installation.state is not AlphaInstallationState.ACTIVE:
        return AlphaInvocationDecision(False, AlphaPolicyReason.INSTALLATION_INACTIVE)
    if facts.installation.dialog_kind not in {DialogKind.GROUP, DialogKind.SUPERGROUP}:
        return AlphaInvocationDecision(False, AlphaPolicyReason.DIALOG_NOT_GROUP)
    if not facts.sender_is_human:
        return AlphaInvocationDecision(False, AlphaPolicyReason.SENDER_NOT_HUMAN)
    if facts.invocation_kind is AlphaInvocationKind.NONE:
        return AlphaInvocationDecision(False, AlphaPolicyReason.NOT_INVOKED)
    if facts.max_turns_per_window < 1 or facts.turns_in_window >= facts.max_turns_per_window:
        return AlphaInvocationDecision(False, AlphaPolicyReason.RATE_LIMITED)
    return AlphaInvocationDecision(True, AlphaPolicyReason.ALLOWED)


@dataclass(frozen=True, slots=True)
class DeliveryGrant:
    """One-use capability bound to exact installation, target, trigger and text."""

    grant_key: str = field(repr=False)
    workspace_id: str
    installation_key: str
    dialog_id: str = field(repr=False)
    trigger_message_id: str = field(repr=False)
    candidate_sha256: str = field(repr=False)
    expires_at: datetime
    used_at: datetime | None = None

    def consume(self, *, now: datetime, candidate_sha256: str) -> DeliveryGrant:
        if self.used_at is not None:
            raise ValueError("delivery_grant_already_used")
        if now.tzinfo is None or self.expires_at.tzinfo is None or now >= self.expires_at:
            raise ValueError("delivery_grant_expired")
        if candidate_sha256 != self.candidate_sha256:
            raise ValueError("delivery_grant_candidate_mismatch")
        return replace(self, used_at=now)


def issue_alpha_delivery_grant(
    *,
    grant_key: str,
    installation: AlphaInstallation,
    decision: AlphaInvocationDecision,
    proposal: AgentShadowProposal,
    trigger_message_id: str,
    now: datetime,
) -> DeliveryGrant:
    """Issue a short one-use grant only after every independent check succeeds."""

    if not decision.allowed:
        raise ValueError("alpha_delivery_not_allowed")
    if installation.state is not AlphaInstallationState.ACTIVE:
        raise ValueError("alpha_installation_inactive")
    if proposal.candidate_sha256 is None or proposal.candidate_text is None:
        raise ValueError("alpha_reply_candidate_required")
    if proposal.deployment_key != installation.deployment_key:
        raise ValueError("alpha_deployment_mismatch")
    if proposal.instruction_content_sha256 != installation.instruction_content_sha256:
        raise ValueError("alpha_instruction_mismatch")
    if now.tzinfo is None:
        raise ValueError("alpha_grant_time_invalid")
    return DeliveryGrant(
        grant_key=grant_key,
        workspace_id=installation.workspace_id,
        installation_key=installation.installation_key,
        dialog_id=installation.dialog_id,
        trigger_message_id=trigger_message_id,
        candidate_sha256=proposal.candidate_sha256,
        expires_at=now + timedelta(minutes=5),
    )


@dataclass(frozen=True, slots=True)
class AgentSendAttempt:
    """Durable-state contract where unknown is terminal and never auto-retried."""

    attempt_key: str
    grant_key: str = field(repr=False)
    state: AgentSendAttemptState = AgentSendAttemptState.RESERVED

    def begin(self) -> AgentSendAttempt:
        if self.state is not AgentSendAttemptState.RESERVED:
            raise ValueError("agent_send_attempt_not_reserved")
        return replace(self, state=AgentSendAttemptState.SENDING)

    def finish(self, state: AgentSendAttemptState) -> AgentSendAttempt:
        if self.state is not AgentSendAttemptState.SENDING:
            raise ValueError("agent_send_attempt_not_sending")
        if state not in {
            AgentSendAttemptState.SENT,
            AgentSendAttemptState.FAILED,
            AgentSendAttemptState.UNKNOWN,
            AgentSendAttemptState.BLOCKED,
        }:
            raise ValueError("agent_send_attempt_terminal_state_invalid")
        return replace(self, state=state)

    @property
    def retryable(self) -> bool:
        return self.state is AgentSendAttemptState.FAILED


assert AgentKey.ALPHA_GROUP.value == "alpha_group"
