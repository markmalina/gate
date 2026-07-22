"""Pure owner-test-group presence and exact delivery authorization contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.application.alpha_shadow_reviews import AlphaShadowReview

_SHA256_LENGTH = 64
_DEFAULT_OWNER_AND_BOT_COUNT = 2
_GRANT_TTL = timedelta(minutes=5)


class OwnerTestPresenceState(StrEnum):
    """Privacy-preserving result of an aggregate group-size observation."""

    OWNER_ONLY = "owner_only"
    THIRD_PARTY_PRESENT = "third_party_present"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OwnerTestPresence:
    """Aggregate presence evidence that never contains participant identity."""

    installation_ref: str
    group_label: str
    member_count: int | None
    state: OwnerTestPresenceState
    observed_at: datetime

    def payload(self) -> dict[str, object]:
        return {
            "installation_ref": self.installation_ref,
            "group_label": self.group_label,
            "member_count": self.member_count,
            "state": self.state.value,
            "observed_at": self.observed_at.isoformat(),
            "participant_identities_exposed": False,
        }


@dataclass(frozen=True, slots=True)
class OwnerTestDeliveryGrantRequest:
    """One-use request bound to an exact existing model proposal."""

    grant_key: str
    turn_ref: str
    installation_ref: str
    candidate_sha256: str
    expires_at: datetime


def evaluate_owner_test_presence(
    *,
    installation_ref: str,
    group_label: str,
    member_count: int | None,
    observed_at: datetime,
    owner_and_bot_count: int = _DEFAULT_OWNER_AND_BOT_COUNT,
) -> OwnerTestPresence:
    """Classify aggregate member count; ambiguous evidence always fails closed."""

    if observed_at.tzinfo is None:
        raise ValueError("owner_test_presence_time_invalid")
    if owner_and_bot_count < 2:
        raise ValueError("owner_test_baseline_invalid")
    if member_count is None or member_count < owner_and_bot_count:
        state = OwnerTestPresenceState.UNKNOWN
    elif member_count == owner_and_bot_count:
        state = OwnerTestPresenceState.OWNER_ONLY
    else:
        state = OwnerTestPresenceState.THIRD_PARTY_PRESENT
    return OwnerTestPresence(
        installation_ref=installation_ref,
        group_label=group_label,
        member_count=member_count,
        state=state,
        observed_at=observed_at,
    )


def authorize_owner_test_delivery(
    *,
    review: AlphaShadowReview,
    presence: OwnerTestPresence,
    allowed_group_labels: frozenset[str],
    expected_candidate_sha256: str,
    grant_key: str,
    now: datetime,
    confirmed: bool,
) -> OwnerTestDeliveryGrantRequest:
    """Authorize only one exact, unused proposal in a currently owner-only group."""

    if not confirmed:
        raise ValueError("owner_test_delivery_confirmation_required")
    if now.tzinfo is None:
        raise ValueError("owner_test_delivery_time_invalid")
    if review.group_label not in allowed_group_labels:
        raise ValueError("owner_test_group_not_authorized")
    if presence.group_label != review.group_label:
        raise ValueError("owner_test_presence_group_mismatch")
    if presence.installation_ref != review.installation_ref:
        raise ValueError("owner_test_presence_installation_mismatch")
    if presence.state is not OwnerTestPresenceState.OWNER_ONLY:
        raise ValueError("owner_test_presence_not_owner_only")
    if review.status != "proposed":
        raise ValueError("owner_test_proposal_not_available")
    if review.candidate_text is None or review.candidate_sha256 is None:
        raise ValueError("owner_test_candidate_required")
    if len(expected_candidate_sha256) != _SHA256_LENGTH:
        raise ValueError("owner_test_candidate_digest_invalid")
    if expected_candidate_sha256 != review.candidate_sha256:
        raise ValueError("owner_test_candidate_digest_mismatch")
    if not grant_key or grant_key != grant_key.strip() or len(grant_key) > 128:
        raise ValueError("owner_test_grant_key_invalid")
    return OwnerTestDeliveryGrantRequest(
        grant_key=grant_key,
        turn_ref=review.turn_ref,
        installation_ref=review.installation_ref,
        candidate_sha256=review.candidate_sha256,
        expires_at=now + _GRANT_TTL,
    )
