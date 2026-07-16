"""Fail-closed admission rules for future Instagram automation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FollowState(StrEnum):
    FOLLOWING = "following"
    NOT_FOLLOWING = "not_following"
    UNKNOWN = "unknown"


class AutomationDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_HUMAN = "require_human"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class AutomationFacts:
    automation_enabled: bool
    kill_switch_active: bool
    approved_copy: bool
    event_is_deduplicated: bool
    messaging_window_open: bool
    follow_state: FollowState


def admit_direct_delivery(facts: AutomationFacts, *, requires_follow: bool) -> AutomationDecision:
    """Admit only a deterministic, pre-approved action with a known entitlement."""

    if not facts.automation_enabled or facts.kill_switch_active:
        return AutomationDecision.BLOCK
    if not facts.event_is_deduplicated or not facts.approved_copy:
        return AutomationDecision.BLOCK
    if not facts.messaging_window_open:
        return AutomationDecision.REQUIRE_HUMAN
    if requires_follow and facts.follow_state is FollowState.UNKNOWN:
        return AutomationDecision.REQUIRE_HUMAN
    if requires_follow and facts.follow_state is FollowState.NOT_FOLLOWING:
        return AutomationDecision.BLOCK
    return AutomationDecision.ALLOW
