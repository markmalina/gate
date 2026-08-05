from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.infrastructure.beta_candidate_binding import (
    BETA_BINDING_CANDIDATE_TEXT,
    BETA_BINDING_CLAIM_NOT_BEFORE,
    BETA_BINDING_GROUP_TITLE,
    BetaCandidateBindingResult,
    extract_beta_binding_candidate,
)


def payload(*, title=BETA_BINDING_GROUP_TITLE, user_id=70001, text=BETA_BINDING_CANDIDATE_TEXT):
    return {
        "message": {
            "chat": {"id": -10001, "type": "supergroup", "title": title},
            "from": {"id": user_id, "is_bot": False},
            "text": text,
        }
    }


def test_exact_post_claim_candidate_is_private_and_accepted():
    candidate = extract_beta_binding_candidate(
        receipt_id=UUID("00000000-0000-0000-0000-000000000485"),
        update_id=485,
        payload=payload(),
        received_at=BETA_BINDING_CLAIM_NOT_BEFORE,
    )
    assert candidate is not None
    assert "-10001" not in repr(candidate)
    assert "70001" not in repr(candidate)


def test_pre_claim_wrong_group_wrong_actor_and_wrong_text_fail_closed():
    cases = [
        (payload(), BETA_BINDING_CLAIM_NOT_BEFORE - timedelta(microseconds=1)),
        (payload(title="Other"), BETA_BINDING_CLAIM_NOT_BEFORE),
        (payload(user_id=-1), BETA_BINDING_CLAIM_NOT_BEFORE),
        (payload(text="almost"), BETA_BINDING_CLAIM_NOT_BEFORE),
    ]
    for value, received_at in cases:
        assert (
            extract_beta_binding_candidate(
                receipt_id=UUID("00000000-0000-0000-0000-000000000485"),
                update_id=485,
                payload=value,
                received_at=received_at,
            )
            is None
        )


def test_redacted_result_never_returns_private_values():
    result = BetaCandidateBindingResult(
        "bound_awaiting_consent", 1, True, True, "-10001"
    ).redacted(prompt_delivery="sent")
    wire = repr(result)
    assert "-10001" not in wire
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
    assert result["consent_digest_exposed"] is False

