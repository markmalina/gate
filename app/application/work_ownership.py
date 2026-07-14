"""Connector-neutral contracts for shadow work-ownership evaluation.

The shadow result is deliberately non-authoritative.  It can identify a bounded
agent candidate, but it cannot suppress human work, create agent work, or grant
delivery.  Authoritative ownership is introduced only after workspace, dialog,
installation, and session identities exist.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

WORK_OWNERSHIP_SHADOW_POLICY_VERSION = "work-ownership-shadow-v1"


class WorkOwnershipState(StrEnum):
    """Canonical ownership states shared by the future authoritative aggregate."""

    HUMAN_MANAGED = "human_managed"
    AGENT_MANAGED = "agent_managed"
    HANDOFF_REQUIRED = "handoff_required"
    PAUSED = "paused"
    IGNORED = "ignored"
    QUARANTINED = "quarantined"


class AgentKey(StrEnum):
    """Stable product agent identities, independent of a model or deployment."""

    ALPHA_GROUP = "alpha_group"
    OKO = "oko"


class OwnershipSignal(StrEnum):
    """Minimized product signal derived by a transport adapter."""

    NONE = "none"
    ALPHA_GROUP_INVOCATION = "alpha_group_invocation"
    OKO_EXACT_ENROLLMENT = "oko_exact_enrollment"


class BusinessScope(StrEnum):
    """Owner Business-account scope relevant to the current product boundary."""

    NOT_APPLICABLE = "not_applicable"
    RU = "ru"
    BY = "by"
    UNKNOWN = "unknown"


class DirectionEvidence(StrEnum):
    """Bounded evidence about who originated the trigger message."""

    UNKNOWN = "unknown"
    HUMAN_INBOUND = "human_inbound"
    OWNER_OUTBOUND = "owner_outbound"
    BOT_OR_SERVICE = "bot_or_service"


class WorkOwnershipReason(StrEnum):
    """Stable, payload-free reasons for a shadow evaluation."""

    DEFAULT_HUMAN = "default_human"
    ALPHA_GROUP_INVOCATION_CANDIDATE = "alpha_group_invocation_candidate"
    OKO_EXACT_ENROLLMENT_CANDIDATE = "oko_exact_enrollment_candidate"
    BY_DEFERRED = "by_deferred"
    CONTROL_TRAFFIC = "control_traffic"
    UNSUPPORTED_UPDATE = "unsupported_update"
    UNRESOLVED_CONTEXT = "unresolved_context"


class OwnershipRequirement(StrEnum):
    """Authoritative checks that a shadow candidate is not allowed to bypass."""

    WORKSPACE = "workspace"
    CONNECTOR_ROLE = "connector_role"
    DIALOG = "dialog"
    DIRECTION = "direction"
    INSTALLATION = "installation"
    DEPLOYMENT = "deployment"
    SESSION = "session"
    HISTORY_REVIEW = "history_review"
    REPLY_CAPABILITY = "reply_capability"


@dataclass(frozen=True, slots=True)
class WorkOwnershipFacts:
    """Minimized connector projection accepted by the pure ownership evaluator."""

    signal: OwnershipSignal = OwnershipSignal.NONE
    business_scope: BusinessScope = BusinessScope.NOT_APPLICABLE
    direction_evidence: DirectionEvidence = DirectionEvidence.UNKNOWN
    control_traffic: bool = False
    unsupported_update: bool = False
    routing_resolved: bool = True


@dataclass(frozen=True, slots=True)
class ShadowWorkOwnershipEvaluation:
    """A redacted proposal that can never become routing authority."""

    proposed_state: WorkOwnershipState
    candidate_agent: AgentKey | None
    reason: WorkOwnershipReason
    unresolved_requirements: tuple[OwnershipRequirement, ...]
    observed_signal: OwnershipSignal = OwnershipSignal.NONE
    policy_version: str = WORK_OWNERSHIP_SHADOW_POLICY_VERSION
    authoritative: Literal[False] = False

    def __post_init__(self) -> None:
        """Reject constructions that would accidentally grant agent authority."""

        if self.authoritative is not False:
            raise ValueError("shadow ownership cannot be authoritative")
        if self.proposed_state in {
            WorkOwnershipState.AGENT_MANAGED,
            WorkOwnershipState.HANDOFF_REQUIRED,
            WorkOwnershipState.PAUSED,
        }:
            raise ValueError("shadow ownership cannot activate an agent state")
        if self.candidate_agent is not None and (
            self.proposed_state is not WorkOwnershipState.HUMAN_MANAGED
            or not self.unresolved_requirements
        ):
            raise ValueError("an agent candidate requires human fallback and unresolved checks")

    def log_fields(self) -> dict[str, object]:
        """Return stable fields that contain no message or provider identifiers."""

        return {
            "proposed_state": self.proposed_state.value,
            "candidate_agent": (
                self.candidate_agent.value if self.candidate_agent is not None else None
            ),
            "reason": self.reason.value,
            "observed_signal": self.observed_signal.value,
            "unresolved_requirements": [
                requirement.value for requirement in self.unresolved_requirements
            ],
            "policy_version": self.policy_version,
            "authoritative": self.authoritative,
        }


def evaluate_shadow_work_ownership(
    facts: WorkOwnershipFacts,
) -> ShadowWorkOwnershipEvaluation:
    """Evaluate one minimized observation without creating or suppressing work."""

    if facts.unsupported_update:
        return _evaluation(
            state=WorkOwnershipState.IGNORED,
            reason=WorkOwnershipReason.UNSUPPORTED_UPDATE,
            signal=facts.signal,
        )
    if facts.control_traffic:
        return _evaluation(
            state=WorkOwnershipState.IGNORED,
            reason=WorkOwnershipReason.CONTROL_TRAFFIC,
            signal=facts.signal,
        )
    if not facts.routing_resolved:
        return _evaluation(
            state=WorkOwnershipState.QUARANTINED,
            reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
            signal=facts.signal,
            requirements=(
                OwnershipRequirement.WORKSPACE,
                OwnershipRequirement.CONNECTOR_ROLE,
                OwnershipRequirement.DIALOG,
            ),
        )

    if facts.signal is OwnershipSignal.ALPHA_GROUP_INVOCATION:
        if facts.business_scope is not BusinessScope.NOT_APPLICABLE:
            return _evaluation(
                state=WorkOwnershipState.QUARANTINED,
                reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
                signal=facts.signal,
                requirements=(OwnershipRequirement.CONNECTOR_ROLE,),
            )
        if facts.direction_evidence is not DirectionEvidence.HUMAN_INBOUND:
            return _evaluation(
                state=WorkOwnershipState.HUMAN_MANAGED,
                reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
                signal=facts.signal,
                requirements=(OwnershipRequirement.DIRECTION,),
            )
        return _evaluation(
            state=WorkOwnershipState.HUMAN_MANAGED,
            candidate=AgentKey.ALPHA_GROUP,
            reason=WorkOwnershipReason.ALPHA_GROUP_INVOCATION_CANDIDATE,
            signal=facts.signal,
            requirements=(
                OwnershipRequirement.WORKSPACE,
                OwnershipRequirement.CONNECTOR_ROLE,
                OwnershipRequirement.DIALOG,
                OwnershipRequirement.INSTALLATION,
                OwnershipRequirement.DEPLOYMENT,
            ),
        )

    if facts.signal is OwnershipSignal.OKO_EXACT_ENROLLMENT:
        if facts.business_scope is BusinessScope.BY:
            return _evaluation(
                state=WorkOwnershipState.HUMAN_MANAGED,
                reason=WorkOwnershipReason.BY_DEFERRED,
                signal=facts.signal,
            )
        if facts.business_scope is not BusinessScope.RU:
            return _evaluation(
                state=WorkOwnershipState.QUARANTINED,
                reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
                signal=facts.signal,
                requirements=(
                    OwnershipRequirement.WORKSPACE,
                    OwnershipRequirement.CONNECTOR_ROLE,
                    OwnershipRequirement.DIALOG,
                ),
            )
        if facts.direction_evidence is DirectionEvidence.UNKNOWN:
            return _evaluation(
                state=WorkOwnershipState.HUMAN_MANAGED,
                reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
                signal=facts.signal,
                requirements=(OwnershipRequirement.DIRECTION,),
            )
        if facts.direction_evidence is not DirectionEvidence.HUMAN_INBOUND:
            return _evaluation(
                state=WorkOwnershipState.HUMAN_MANAGED,
                reason=WorkOwnershipReason.DEFAULT_HUMAN,
                signal=facts.signal,
            )
        return _evaluation(
            state=WorkOwnershipState.HUMAN_MANAGED,
            candidate=AgentKey.OKO,
            reason=WorkOwnershipReason.OKO_EXACT_ENROLLMENT_CANDIDATE,
            signal=facts.signal,
            requirements=(
                OwnershipRequirement.WORKSPACE,
                OwnershipRequirement.CONNECTOR_ROLE,
                OwnershipRequirement.DIALOG,
                OwnershipRequirement.INSTALLATION,
                OwnershipRequirement.DEPLOYMENT,
                OwnershipRequirement.SESSION,
                OwnershipRequirement.HISTORY_REVIEW,
                OwnershipRequirement.REPLY_CAPABILITY,
            ),
        )

    return _evaluation(
        state=WorkOwnershipState.HUMAN_MANAGED,
        reason=WorkOwnershipReason.DEFAULT_HUMAN,
    )


def _evaluation(
    *,
    state: WorkOwnershipState,
    reason: WorkOwnershipReason,
    candidate: AgentKey | None = None,
    signal: OwnershipSignal = OwnershipSignal.NONE,
    requirements: tuple[OwnershipRequirement, ...] = (),
) -> ShadowWorkOwnershipEvaluation:
    return ShadowWorkOwnershipEvaluation(
        proposed_state=state,
        candidate_agent=candidate,
        reason=reason,
        unresolved_requirements=requirements,
        observed_signal=signal,
    )
