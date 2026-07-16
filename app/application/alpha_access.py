"""Channel-neutral, fail-closed access policy for future Alpha installations.

This module is deliberately pure.  It neither knows Telegram provider identifiers nor
performs ingress, persistence, model, or delivery work.  Existing group execution keeps
using ``alpha_group`` until a later, separately reviewed integration release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.application.alpha_group import AlphaInstallationState
from app.application.canonical_messaging import DialogKind

_OPAQUE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{11,127}\Z")
_SUPPORTED_DIALOG_KINDS = {
    DialogKind.PRIVATE,
    DialogKind.GROUP,
    DialogKind.SUPERGROUP,
}
_GROUP_DIALOG_KINDS = {DialogKind.GROUP, DialogKind.SUPERGROUP}
MAX_ALPHA_TURNS_PER_WINDOW = 100
MAX_ALPHA_RATE_WINDOW_SECONDS = 86_400
MAX_ALPHA_TOKENS_PER_TURN = 32_768


class AlphaProfile(StrEnum):
    """Instruction/personality profile selected for one installation."""

    PUBLIC = "public"
    MARK_PRIVATE = "mark_private"


class AlphaDataScope(StrEnum):
    """Maximum data boundary visible to the selected Alpha profile."""

    INSTALLATION = "installation"
    MARK_PRIVATE = "mark_private"


class AlphaEventKind(StrEnum):
    DISCOVERY = "discovery"
    MESSAGE = "message"


class AlphaMessageOrigin(StrEnum):
    INBOUND = "inbound"
    PROACTIVE = "proactive"


class AlphaSenderKind(StrEnum):
    HUMAN = "human"
    BOT = "bot"
    SYSTEM = "system"


class AlphaContentKind(StrEnum):
    NONE = "none"
    TEXT = "text"
    VOICE = "voice"
    UNSUPPORTED = "unsupported"


class AlphaInvocationKind(StrEnum):
    COMMAND = "command"
    MENTION = "mention"
    REPLY_TO_ALPHA = "reply_to_alpha"
    NONE = "none"


class AlphaAccessRoute(StrEnum):
    """The only three outcomes the future ingress adapter may act upon."""

    BLOCKED = "blocked"
    LOCAL_ONLY = "local_only"
    PROVIDER = "provider"


class AlphaAccessReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_FACTS = "invalid_facts"
    CONNECTOR_DISABLED = "connector_disabled"
    PROACTIVE_FORBIDDEN = "proactive_forbidden"
    DIALOG_UNSUPPORTED = "dialog_unsupported"
    DIALOG_MISMATCH = "dialog_mismatch"
    DISCOVERY_ONLY = "discovery_only"
    SENDER_NOT_HUMAN = "sender_not_human"
    MARK_PRIVATE_REQUIRES_PRIVATE_DIALOG = "mark_private_requires_private_dialog"
    MARK_PRIVATE_REQUIRES_OWNER = "mark_private_requires_owner"
    START_LOCAL_ONLY = "start_local_only"
    INSTALLATION_DISCOVERED = "installation_discovered"
    INSTALLATION_PENDING_APPROVAL = "installation_pending_approval"
    INSTALLATION_PAUSED = "installation_paused"
    INSTALLATION_REVOKED = "installation_revoked"
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"
    CONSENT_REQUIRED = "consent_required"
    CONSENT_VERSION_MISMATCH = "consent_version_mismatch"
    CONTENT_UNSUPPORTED = "content_unsupported"
    VOICE_REPLY_REQUIRED = "voice_reply_required"
    NOT_INVOKED = "not_invoked"


class AlphaLifecycleAction(StrEnum):
    REQUEST_APPROVAL = "request_approval"
    APPROVE = "approve"
    PAUSE = "pause"
    RESUME = "resume"
    REVOKE = "revoke"


class AlphaTransitionReason(StrEnum):
    APPLIED = "applied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    EXPECTED_STATE_MISMATCH = "expected_state_mismatch"
    INVALID_TRANSITION = "invalid_transition"
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"


@dataclass(frozen=True, slots=True)
class AlphaProfileScope:
    """A profile and its data boundary; invalid cross-scope pairs cannot exist."""

    profile: AlphaProfile = AlphaProfile.PUBLIC
    data_scope: AlphaDataScope = AlphaDataScope.INSTALLATION

    def __post_init__(self) -> None:
        expected_scope = (
            AlphaDataScope.INSTALLATION
            if self.profile is AlphaProfile.PUBLIC
            else AlphaDataScope.MARK_PRIVATE
        )
        if self.data_scope is not expected_scope:
            raise ValueError("alpha_profile_data_scope_mismatch")


@dataclass(frozen=True, slots=True)
class AlphaInstallationQuota:
    """The durable per-installation limits the runtime must reserve transactionally."""

    max_turns_per_window: int = 5
    rate_window_seconds: int = 60
    max_tokens_per_turn: int = 2048

    def __post_init__(self) -> None:
        if (
            type(self.max_turns_per_window) is not int
            or not 1 <= self.max_turns_per_window <= MAX_ALPHA_TURNS_PER_WINDOW
        ):
            raise ValueError(
                f"max_turns_per_window must be between 1 and {MAX_ALPHA_TURNS_PER_WINDOW}"
            )
        if (
            type(self.rate_window_seconds) is not int
            or not 1 <= self.rate_window_seconds <= MAX_ALPHA_RATE_WINDOW_SECONDS
        ):
            raise ValueError(
                "rate_window_seconds must be between "
                f"1 and {MAX_ALPHA_RATE_WINDOW_SECONDS}"
            )
        if (
            type(self.max_tokens_per_turn) is not int
            or not 1 <= self.max_tokens_per_turn <= MAX_ALPHA_TOKENS_PER_TURN
        ):
            raise ValueError(
                f"max_tokens_per_turn must be between 1 and {MAX_ALPHA_TOKENS_PER_TURN}"
            )


@dataclass(frozen=True, slots=True)
class AlphaAccessInstallation:
    """Owner-safe access policy view, containing no provider or sender identifier."""

    installation_ref: str
    dialog_label: str
    dialog_kind: DialogKind
    profile_scope: AlphaProfileScope = field(default_factory=AlphaProfileScope)
    quota: AlphaInstallationQuota = field(default_factory=AlphaInstallationQuota)
    state: AlphaInstallationState = AlphaInstallationState.DISCOVERED
    owner_approved: bool = False
    required_consent_version: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation_ref, str)
            or _OPAQUE_REF.fullmatch(self.installation_ref) is None
            or self.installation_ref.isdecimal()
        ):
            raise ValueError("installation_ref must be a bounded opaque reference")
        _require_label(self.dialog_label)
        if not isinstance(self.dialog_kind, DialogKind) or self.dialog_kind not in (
            _SUPPORTED_DIALOG_KINDS
        ):
            raise ValueError("Alpha access supports private, group, and supergroup dialogs")
        if not isinstance(self.profile_scope, AlphaProfileScope):
            raise ValueError("profile_scope must be an AlphaProfileScope")
        if not isinstance(self.quota, AlphaInstallationQuota):
            raise ValueError("quota must be an AlphaInstallationQuota")
        if not isinstance(self.state, AlphaInstallationState):
            raise ValueError("state must be an AlphaInstallationState")
        if type(self.owner_approved) is not bool:
            raise ValueError("owner_approved must be a boolean")
        if self.dialog_kind is DialogKind.PRIVATE:
            _require_version(self.required_consent_version, "required_consent_version")
        elif self.required_consent_version is not None:
            raise ValueError("group installations do not accept private-user consent versions")
        if (
            self.profile_scope.profile is AlphaProfile.MARK_PRIVATE
            and self.dialog_kind is not DialogKind.PRIVATE
        ):
            raise ValueError("mark_private profile requires a private installation")


@dataclass(frozen=True, slots=True)
class AlphaUserConsent:
    """Versioned, user-specific consent fact supplied by the persistence adapter."""

    granted: bool = False
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if type(self.granted) is not bool:
            raise ValueError("granted must be a boolean")
        if self.granted:
            _require_version(self.policy_version, "policy_version")
        elif self.policy_version is not None:
            _require_version(self.policy_version, "policy_version")


@dataclass(frozen=True, slots=True)
class AlphaAccessFacts:
    """Provider-free facts required to make one deterministic access decision."""

    installation: AlphaAccessInstallation
    dialog_kind: DialogKind
    event_kind: AlphaEventKind
    origin: AlphaMessageOrigin
    sender_kind: AlphaSenderKind
    content_kind: AlphaContentKind
    connector_enabled: bool = False
    invocation_kind: AlphaInvocationKind = AlphaInvocationKind.NONE
    is_start: bool = False
    actor_is_owner: bool = False
    consent: AlphaUserConsent | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.installation, AlphaAccessInstallation):
            raise ValueError("installation must be an AlphaAccessInstallation")


@dataclass(frozen=True, slots=True)
class AlphaAccessDecision:
    route: AlphaAccessRoute
    reason: AlphaAccessReason
    profile: AlphaProfile
    data_scope: AlphaDataScope
    quota: AlphaInstallationQuota

    @property
    def provider_allowed(self) -> bool:
        return self.route is AlphaAccessRoute.PROVIDER

    @property
    def local_response_allowed(self) -> bool:
        return self.route is AlphaAccessRoute.LOCAL_ONLY


@dataclass(frozen=True, slots=True)
class TransitionAlphaInstallationCommand:
    action: AlphaLifecycleAction
    expected_state: AlphaInstallationState
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class AlphaTransitionResult:
    applied: bool
    reason: AlphaTransitionReason
    installation: AlphaAccessInstallation


def evaluate_alpha_access(facts: AlphaAccessFacts) -> AlphaAccessDecision:
    """Return a provider-capability decision; every omitted/unknown fact denies access."""

    installation = facts.installation
    if not _facts_are_typed(facts):
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.INVALID_FACTS,
        )
    if facts.connector_enabled is not True:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.CONNECTOR_DISABLED,
        )
    if facts.origin is not AlphaMessageOrigin.INBOUND:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.PROACTIVE_FORBIDDEN,
        )
    if facts.dialog_kind not in _SUPPORTED_DIALOG_KINDS:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.DIALOG_UNSUPPORTED,
        )
    if facts.dialog_kind is not installation.dialog_kind:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.DIALOG_MISMATCH,
        )
    if installation.state is AlphaInstallationState.REVOKED:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.INSTALLATION_REVOKED,
        )
    if facts.event_kind is AlphaEventKind.DISCOVERY:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.DISCOVERY_ONLY,
        )
    if facts.sender_kind is not AlphaSenderKind.HUMAN:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.SENDER_NOT_HUMAN,
        )

    profile = installation.profile_scope.profile
    if profile is AlphaProfile.MARK_PRIVATE:
        if facts.dialog_kind is not DialogKind.PRIVATE:
            return _decision(
                installation,
                AlphaAccessRoute.BLOCKED,
                AlphaAccessReason.MARK_PRIVATE_REQUIRES_PRIVATE_DIALOG,
            )
        if facts.actor_is_owner is not True:
            return _decision(
                installation,
                AlphaAccessRoute.BLOCKED,
                AlphaAccessReason.MARK_PRIVATE_REQUIRES_OWNER,
            )

    if facts.dialog_kind is DialogKind.PRIVATE and facts.is_start:
        # /start is an application-owned consent/onboarding interaction.  Its text is
        # never model input, including after a consent record already exists.
        return _decision(
            installation,
            AlphaAccessRoute.LOCAL_ONLY,
            AlphaAccessReason.START_LOCAL_ONLY,
        )

    inactive_reason = _inactive_reason(installation.state)
    if inactive_reason is not None:
        return _decision(installation, AlphaAccessRoute.BLOCKED, inactive_reason)
    if installation.owner_approved is not True:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.OWNER_APPROVAL_REQUIRED,
        )

    if facts.dialog_kind is DialogKind.PRIVATE:
        consent_reason = _consent_reason(
            facts.consent,
            required_version=installation.required_consent_version,
        )
        if consent_reason is not None:
            return _decision(installation, AlphaAccessRoute.LOCAL_ONLY, consent_reason)

    if facts.content_kind not in {AlphaContentKind.TEXT, AlphaContentKind.VOICE}:
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.CONTENT_UNSUPPORTED,
        )
    if (
        facts.dialog_kind in _GROUP_DIALOG_KINDS
        and facts.content_kind is AlphaContentKind.VOICE
        and facts.invocation_kind is not AlphaInvocationKind.REPLY_TO_ALPHA
    ):
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.VOICE_REPLY_REQUIRED,
        )
    if (
        facts.dialog_kind in _GROUP_DIALOG_KINDS
        and facts.invocation_kind is AlphaInvocationKind.NONE
    ):
        return _decision(
            installation,
            AlphaAccessRoute.BLOCKED,
            AlphaAccessReason.NOT_INVOKED,
        )
    return _decision(installation, AlphaAccessRoute.PROVIDER, AlphaAccessReason.ALLOWED)


def transition_alpha_installation(
    installation: AlphaAccessInstallation,
    command: TransitionAlphaInstallationCommand,
) -> AlphaTransitionResult:
    """Apply an exact-state, explicitly confirmed lifecycle transition in memory."""

    if command.confirmed is not True:
        return AlphaTransitionResult(
            False,
            AlphaTransitionReason.CONFIRMATION_REQUIRED,
            installation,
        )
    if installation.state is not command.expected_state:
        return AlphaTransitionResult(
            False,
            AlphaTransitionReason.EXPECTED_STATE_MISMATCH,
            installation,
        )

    target = _TRANSITIONS.get((installation.state, command.action))
    if target is None:
        return AlphaTransitionResult(
            False,
            AlphaTransitionReason.INVALID_TRANSITION,
            installation,
        )
    if command.action is AlphaLifecycleAction.RESUME and not installation.owner_approved:
        return AlphaTransitionResult(
            False,
            AlphaTransitionReason.OWNER_APPROVAL_REQUIRED,
            installation,
        )

    owner_approved = installation.owner_approved
    if command.action is AlphaLifecycleAction.APPROVE:
        owner_approved = True
    elif command.action is AlphaLifecycleAction.REVOKE:
        owner_approved = False
    updated = replace(
        installation,
        state=target,
        owner_approved=owner_approved,
    )
    return AlphaTransitionResult(True, AlphaTransitionReason.APPLIED, updated)


_TRANSITIONS: dict[
    tuple[AlphaInstallationState, AlphaLifecycleAction], AlphaInstallationState
] = {
    (
        AlphaInstallationState.DISCOVERED,
        AlphaLifecycleAction.REQUEST_APPROVAL,
    ): AlphaInstallationState.PENDING_APPROVAL,
    (
        AlphaInstallationState.PENDING_APPROVAL,
        AlphaLifecycleAction.APPROVE,
    ): AlphaInstallationState.ACTIVE,
    (
        AlphaInstallationState.ACTIVE,
        AlphaLifecycleAction.PAUSE,
    ): AlphaInstallationState.PAUSED,
    (
        AlphaInstallationState.PAUSED,
        AlphaLifecycleAction.RESUME,
    ): AlphaInstallationState.ACTIVE,
    (
        AlphaInstallationState.DISCOVERED,
        AlphaLifecycleAction.REVOKE,
    ): AlphaInstallationState.REVOKED,
    (
        AlphaInstallationState.PENDING_APPROVAL,
        AlphaLifecycleAction.REVOKE,
    ): AlphaInstallationState.REVOKED,
    (
        AlphaInstallationState.ACTIVE,
        AlphaLifecycleAction.REVOKE,
    ): AlphaInstallationState.REVOKED,
    (
        AlphaInstallationState.PAUSED,
        AlphaLifecycleAction.REVOKE,
    ): AlphaInstallationState.REVOKED,
}


def _decision(
    installation: AlphaAccessInstallation,
    route: AlphaAccessRoute,
    reason: AlphaAccessReason,
) -> AlphaAccessDecision:
    return AlphaAccessDecision(
        route=route,
        reason=reason,
        profile=installation.profile_scope.profile,
        data_scope=installation.profile_scope.data_scope,
        quota=installation.quota,
    )


def _facts_are_typed(facts: AlphaAccessFacts) -> bool:
    return (
        isinstance(facts.installation, AlphaAccessInstallation)
        and isinstance(facts.dialog_kind, DialogKind)
        and isinstance(facts.event_kind, AlphaEventKind)
        and isinstance(facts.origin, AlphaMessageOrigin)
        and isinstance(facts.sender_kind, AlphaSenderKind)
        and isinstance(facts.content_kind, AlphaContentKind)
        and isinstance(facts.invocation_kind, AlphaInvocationKind)
        and type(facts.connector_enabled) is bool
        and type(facts.is_start) is bool
        and type(facts.actor_is_owner) is bool
        and (facts.consent is None or isinstance(facts.consent, AlphaUserConsent))
    )


def _inactive_reason(state: AlphaInstallationState) -> AlphaAccessReason | None:
    return {
        AlphaInstallationState.DISCOVERED: AlphaAccessReason.INSTALLATION_DISCOVERED,
        AlphaInstallationState.PENDING_APPROVAL: (
            AlphaAccessReason.INSTALLATION_PENDING_APPROVAL
        ),
        AlphaInstallationState.PAUSED: AlphaAccessReason.INSTALLATION_PAUSED,
        AlphaInstallationState.REVOKED: AlphaAccessReason.INSTALLATION_REVOKED,
    }.get(state)


def _consent_reason(
    consent: AlphaUserConsent | None,
    *,
    required_version: str | None,
) -> AlphaAccessReason | None:
    if consent is None or not consent.granted:
        return AlphaAccessReason.CONSENT_REQUIRED
    if consent.policy_version != required_version:
        return AlphaAccessReason.CONSENT_VERSION_MISMATCH
    return None


def _require_label(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 255
        or value != value.strip()
    ):
        raise ValueError("dialog_label must be bounded non-empty text")


def _require_version(value: str | None, field_name: str) -> None:
    if (
        value is None
        or not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
    ):
        raise ValueError(f"{field_name} must be a bounded version token")
