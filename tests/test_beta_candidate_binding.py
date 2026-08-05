from datetime import timedelta
from uuid import UUID

from app.infrastructure.beta_candidate_binding import (
    BETA_BINDING_EVIDENCE_NOT_BEFORE,
    BETA_BINDING_EVIDENCE_TEXT,
    BETA_BINDING_GROUP_TITLE,
    BETA_CONSENT_PROMPT,
    BETA_CONSENT_REPLY_MARKUP,
    BetaCandidateBindingResult,
    extract_beta_binding_candidate,
    extract_beta_callback_owner_candidate,
    extract_beta_group_candidate,
    extract_beta_katya_creator,
)


def payload(
    *,
    title=BETA_BINDING_GROUP_TITLE,
    user_id=70001,
    first_name="Катя",
    text=BETA_BINDING_EVIDENCE_TEXT,
):
    return {
        "message": {
            "chat": {"id": -10001, "type": "supergroup", "title": title},
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": first_name,
            },
            "text": text,
        }
    }


def test_exact_prior_consent_is_private_identity_evidence_only():
    candidate = extract_beta_binding_candidate(
        receipt_id=UUID("00000000-0000-0000-0000-000000000485"),
        update_id=485,
        payload=payload(),
        received_at=BETA_BINDING_EVIDENCE_NOT_BEFORE,
    )
    assert candidate is not None
    assert "-10001" not in repr(candidate)
    assert "70001" not in repr(candidate)


def test_pre_evidence_wrong_group_actor_name_and_text_fail_closed():
    cases = [
        (
            payload(),
            BETA_BINDING_EVIDENCE_NOT_BEFORE - timedelta(microseconds=1),
        ),
        (payload(title="Other"), BETA_BINDING_EVIDENCE_NOT_BEFORE),
        (payload(user_id=-1), BETA_BINDING_EVIDENCE_NOT_BEFORE),
        (payload(first_name="Марк"), BETA_BINDING_EVIDENCE_NOT_BEFORE),
        (payload(text="almost"), BETA_BINDING_EVIDENCE_NOT_BEFORE),
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


def test_prompt_requires_no_typed_command_and_has_yes_no_buttons():
    assert "ты согласна" in BETA_CONSENT_PROMPT
    assert "отправь" not in BETA_CONSENT_PROMPT
    assert BETA_CONSENT_REPLY_MARKUP == {
        "inline_keyboard": [
            [
                {
                    "text": "Да, согласна",
                    "callback_data": "beta:katya:consent:yes",
                },
                {
                    "text": "Нет",
                    "callback_data": "beta:katya:consent:no",
                },
            ]
        ]
    }


def test_redacted_result_never_returns_private_values():
    result = BetaCandidateBindingResult(
        "bound_awaiting_consent", 1, True, True, "-10001"
    ).redacted(prompt_delivery="sent")
    wire = repr(result)
    assert "-10001" not in wire
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
    assert result["consent_digest_exposed"] is False


def membership_payload(*, title=BETA_BINDING_GROUP_TITLE, status="administrator"):
    return {
        "my_chat_member": {
            "chat": {"id": -10001, "type": "supergroup", "title": title},
            "new_chat_member": {
                "status": status,
                "user": {"id": 90001, "is_bot": True, "first_name": "Бета"},
            },
        }
    }


def test_exact_membership_receipt_selects_group_without_exposing_id():
    candidate = extract_beta_group_candidate(
        payload=membership_payload(),
        received_at=BETA_BINDING_EVIDENCE_NOT_BEFORE,
    )
    assert candidate is not None
    assert "-10001" not in repr(candidate)


def test_membership_receipt_rejects_wrong_group_or_inactive_bot():
    assert extract_beta_group_candidate(
        payload=membership_payload(title="Другая группа"),
        received_at=BETA_BINDING_EVIDENCE_NOT_BEFORE,
    ) is None
    assert extract_beta_group_candidate(
        payload=membership_payload(status="left"),
        received_at=BETA_BINDING_EVIDENCE_NOT_BEFORE,
    ) is None


def test_exact_katya_first_name_with_surname_is_selected_privately():
    administrators = [
        {
            "status": "creator",
            "user": {
                "id": 70001,
                "is_bot": False,
                "first_name": "Катя Малиновская ❤️",
            },
        },
        {
            "status": "administrator",
            "user": {
                "id": 70002,
                "is_bot": False,
                "first_name": "Марк",
            },
        },
    ]
    result = extract_beta_katya_creator(administrators)
    assert result == "70001"


def test_creator_resolution_rejects_zero_or_multiple_exact_matches():
    assert extract_beta_katya_creator([]) is None
    assert extract_beta_katya_creator(
        [
            {
                "status": "creator",
                "user": {"id": 1, "is_bot": False, "first_name": "Катя"},
            },
            {
                "status": "creator",
                "user": {"id": 2, "is_bot": False, "first_name": "Катя"},
            },
        ]
    ) is None


def test_project_start_membership_receipt_before_activation_window_is_eligible():
    candidate = extract_beta_group_candidate(
        payload=membership_payload(),
        received_at=BETA_BINDING_EVIDENCE_NOT_BEFORE - timedelta(days=1),
    )
    assert candidate is not None
    assert "-10001" not in repr(candidate)


def test_creator_resolution_rejects_name_that_only_contains_katya_later():
    assert extract_beta_katya_creator(
        [
            {
                "status": "creator",
                "user": {
                    "id": 70001,
                    "is_bot": False,
                    "first_name": "Мама Кати",
                },
            }
        ]
    ) is None


def test_exact_katya_button_callback_is_private_owner_candidate():
    candidate = extract_beta_callback_owner_candidate(
        {
            "callback_query": {
                "data": "beta:katya:consent:yes",
                "from": {
                    "id": 70001,
                    "is_bot": False,
                    "first_name": "Катя Малиновская ❤️",
                },
                "message": {
                    "chat": {
                        "id": -10001,
                        "type": "supergroup",
                        "title": BETA_BINDING_GROUP_TITLE,
                    }
                },
            }
        }
    )
    assert candidate is not None
    assert "-10001" not in repr(candidate)
    assert "70001" not in repr(candidate)


def test_callback_owner_candidate_rejects_wrong_actor_or_group():
    assert extract_beta_callback_owner_candidate(
        {
            "callback_query": {
                "data": "beta:katya:consent:yes",
                "from": {
                    "id": 70001,
                    "is_bot": False,
                    "first_name": "Марк",
                },
                "message": {
                    "chat": {
                        "id": -10001,
                        "type": "supergroup",
                        "title": BETA_BINDING_GROUP_TITLE,
                    }
                },
            }
        }
    ) is None
