"""Default-deny discovery and addressing policy for Alpha installations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AlphaAccessPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AlphaDialogKind(StrEnum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class AlphaAddressingSignal(StrEnum):
    COMMAND = "command"
    MENTION = "mention"
    REPLY_TO_ALPHA = "reply_to_alpha"
    DIRECT_MESSAGE = "direct_message"


class AlphaInstallationState(StrEnum):
    DISCOVERED = "discovered"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class AlphaDiscoveryCandidate:
    workspace_key: str
    connector_key: str
    dialog_ref: str = field(repr=False)
    dialog_kind: AlphaDialogKind
    dialog_label: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "alpha_workspace_key_invalid", 128)
        _require_token(self.connector_key, "alpha_connector_key_invalid", 128)
        _require_token(self.dialog_ref, "alpha_dialog_ref_invalid", 255)
        _require_text(self.dialog_label, "alpha_dialog_label_invalid", 255)


@dataclass(frozen=True, slots=True)
class AlphaDiscoveryDecision:
    accepted: bool
    reason_code: str
    initial_state: AlphaInstallationState | None

    def __post_init__(self) -> None:
        if self.accepted != (self.initial_state is not None):
            raise AlphaAccessPolicyError("alpha_discovery_decision_inconsistent")
        if self.initial_state is AlphaInstallationState.ACTIVE:
            raise AlphaAccessPolicyError("alpha_discovery_cannot_auto_activate")


def evaluate_discovery(
    candidate: AlphaDiscoveryCandidate,
) -> AlphaDiscoveryDecision:
    """Discover groups and private chats, but never activate without owner approval."""

    if candidate.dialog_kind is AlphaDialogKind.CHANNEL:
        return AlphaDiscoveryDecision(
            accepted=False,
            reason_code="alpha_channel_not_supported",
            initial_state=None,
        )
    return AlphaDiscoveryDecision(
        accepted=True,
        reason_code="alpha_owner_approval_required",
        initial_state=AlphaInstallationState.PENDING_APPROVAL,
    )


@dataclass(frozen=True, slots=True)
class AlphaInboundTurn:
    dialog_kind: AlphaDialogKind
    installation_state: AlphaInstallationState
    signal: AlphaAddressingSignal
    sender_is_bot_or_service: bool
    connector_ready: bool
    provider_ready: bool
    billing_admitted: bool


@dataclass(frozen=True, slots=True)
class AlphaTurnDecision:
    admitted: bool
    reason_code: str
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if self.telegram_delivery_allowed:
            raise AlphaAccessPolicyError("alpha_policy_cannot_grant_delivery")


def evaluate_inbound_turn(turn: AlphaInboundTurn) -> AlphaTurnDecision:
    """Admit only explicitly addressed turns; delivery remains a separate capability."""

    if turn.installation_state is not AlphaInstallationState.ACTIVE:
        return AlphaTurnDecision(False, "alpha_installation_not_active")
    if turn.sender_is_bot_or_service:
        return AlphaTurnDecision(False, "alpha_sender_not_human")
    if not turn.connector_ready:
        return AlphaTurnDecision(False, "alpha_connector_not_ready")
    if not turn.provider_ready:
        return AlphaTurnDecision(False, "alpha_provider_not_ready")
    if not turn.billing_admitted:
        return AlphaTurnDecision(False, "alpha_billing_not_admitted")

    if turn.dialog_kind in {AlphaDialogKind.GROUP, AlphaDialogKind.SUPERGROUP}:
        allowed_signals = {
            AlphaAddressingSignal.COMMAND,
            AlphaAddressingSignal.MENTION,
            AlphaAddressingSignal.REPLY_TO_ALPHA,
        }
    elif turn.dialog_kind is AlphaDialogKind.PRIVATE:
        allowed_signals = {
            AlphaAddressingSignal.COMMAND,
            AlphaAddressingSignal.REPLY_TO_ALPHA,
            AlphaAddressingSignal.DIRECT_MESSAGE,
        }
    else:
        return AlphaTurnDecision(False, "alpha_dialog_kind_not_supported")

    if turn.signal not in allowed_signals:
        return AlphaTurnDecision(False, "alpha_not_addressed")
    return AlphaTurnDecision(True, "alpha_turn_admitted")


def _require_token(value: str, code: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise AlphaAccessPolicyError(code)


def _require_text(value: str, code: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise AlphaAccessPolicyError(code)
