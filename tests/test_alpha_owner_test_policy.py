"""Tests for owner-only Alpha test-group delivery policy."""

from datetime import UTC, datetime

import pytest
from app.application.alpha_owner_test_policy import (
    OwnerTestPresenceState,
    authorize_owner_test_delivery,
    evaluate_owner_test_presence,
)
from app.application.alpha_shadow_reviews import AlphaShadowReview

NOW = datetime(2026, 7, 22, 16, 10, tzinfo=UTC)
DIGEST = "a" * 64
ALLOWED_GROUPS = frozenset({"Alpha Group", "Строй αV", "Майя Альфа"})


def _review(
    *,
    group_label: str = "Alpha Group",
    installation_ref: str = "installation-opaque",
    status: str = "proposed",
    digest: str | None = DIGEST,
) -> AlphaShadowReview:
    return AlphaShadowReview(
        turn_ref="turn-opaque",
        installation_ref=installation_ref,
        group_label=group_label,
        deployment_key="alpha-model-v2-openai",
        status=status,
        outcome="reply",
        result_code="ok",
        trigger_text="/alpha Ответь одним сообщением: Альфа на связи.",
        input_origin="telegram_text",
        candidate_text="Альфа на связи." if digest is not None else None,
        candidate_sha256=digest,
        context_message_count=25,
        created_at=NOW,
    )


def test_exact_owner_only_proposal_is_authorized() -> None:
    presence = evaluate_owner_test_presence(
        installation_ref="installation-opaque",
        group_label="Alpha Group",
        member_count=2,
        observed_at=NOW,
    )

    grant = authorize_owner_test_delivery(
        review=_review(),
        presence=presence,
        allowed_group_labels=ALLOWED_GROUPS,
        expected_candidate_sha256=DIGEST,
        grant_key="grant-opaque",
        now=NOW,
        confirmed=True,
    )

    assert presence.state is OwnerTestPresenceState.OWNER_ONLY
    assert grant.turn_ref == "turn-opaque"
    assert grant.installation_ref == "installation-opaque"
    assert grant.candidate_sha256 == DIGEST
    assert grant.expires_at > NOW
    assert presence.payload()["participant_identities_exposed"] is False
    assert set(presence.payload()) == {
        "installation_ref",
        "group_label",
        "member_count",
        "state",
        "observed_at",
        "participant_identities_exposed",
    }


@pytest.mark.parametrize("member_count", [0, 1, None])
def test_ambiguous_presence_fails_closed(member_count: int | None) -> None:
    presence = evaluate_owner_test_presence(
        installation_ref="installation-opaque",
        group_label="Alpha Group",
        member_count=member_count,
        observed_at=NOW,
    )

    assert presence.state is OwnerTestPresenceState.UNKNOWN
    with pytest.raises(ValueError, match="owner_test_presence_not_owner_only"):
        authorize_owner_test_delivery(
            review=_review(),
            presence=presence,
            allowed_group_labels=ALLOWED_GROUPS,
            expected_candidate_sha256=DIGEST,
            grant_key="grant-opaque",
            now=NOW,
            confirmed=True,
        )


def test_third_party_presence_blocks_delivery() -> None:
    presence = evaluate_owner_test_presence(
        installation_ref="installation-opaque",
        group_label="Alpha Group",
        member_count=3,
        observed_at=NOW,
    )

    assert presence.state is OwnerTestPresenceState.THIRD_PARTY_PRESENT
    with pytest.raises(ValueError, match="owner_test_presence_not_owner_only"):
        authorize_owner_test_delivery(
            review=_review(),
            presence=presence,
            allowed_group_labels=ALLOWED_GROUPS,
            expected_candidate_sha256=DIGEST,
            grant_key="grant-opaque",
            now=NOW,
            confirmed=True,
        )


@pytest.mark.parametrize(
    ("review", "presence", "expected_digest", "confirmed", "error"),
    [
        (
            _review(group_label="Люди"),
            evaluate_owner_test_presence(
                installation_ref="installation-opaque",
                group_label="Люди",
                member_count=2,
                observed_at=NOW,
            ),
            DIGEST,
            True,
            "owner_test_group_not_authorized",
        ),
        (
            _review(),
            evaluate_owner_test_presence(
                installation_ref="other-installation",
                group_label="Alpha Group",
                member_count=2,
                observed_at=NOW,
            ),
            DIGEST,
            True,
            "owner_test_presence_installation_mismatch",
        ),
        (
            _review(),
            evaluate_owner_test_presence(
                installation_ref="installation-opaque",
                group_label="Alpha Group",
                member_count=2,
                observed_at=NOW,
            ),
            "b" * 64,
            True,
            "owner_test_candidate_digest_mismatch",
        ),
        (
            _review(status="sent"),
            evaluate_owner_test_presence(
                installation_ref="installation-opaque",
                group_label="Alpha Group",
                member_count=2,
                observed_at=NOW,
            ),
            DIGEST,
            True,
            "owner_test_proposal_not_available",
        ),
        (
            _review(),
            evaluate_owner_test_presence(
                installation_ref="installation-opaque",
                group_label="Alpha Group",
                member_count=2,
                observed_at=NOW,
            ),
            DIGEST,
            False,
            "owner_test_delivery_confirmation_required",
        ),
    ],
)
def test_delivery_policy_rejects_mutated_or_unconfirmed_input(
    review: AlphaShadowReview,
    presence,
    expected_digest: str,
    confirmed: bool,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        authorize_owner_test_delivery(
            review=review,
            presence=presence,
            allowed_group_labels=ALLOWED_GROUPS,
            expected_candidate_sha256=expected_digest,
            grant_key="grant-opaque",
            now=NOW,
            confirmed=confirmed,
        )
