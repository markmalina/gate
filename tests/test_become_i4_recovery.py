from __future__ import annotations

import pytest
from app.application.become_i4_chain import canonical_digest
from app.infrastructure.become_i4_chain import (
    MAYAKI_SOURCE_SHA,
    OKO_SOURCE_SHA,
    TAKE_SOURCE_SHA,
)
from app.infrastructure.become_i4_recovery import (
    RECOVERY_TARGET_SOURCE_SHA,
    build_recovery_payload,
    validate_recovery_attempt_replay,
    validate_recovery_event_sequence,
    validate_recovery_replay,
)


def _payload() -> dict[str, object]:
    return build_recovery_payload(
        correlation_ref="synthetic://correlation/voice/" + "1" * 32,
        original_request_digest="2" * 64,
        original_receipt_digest="3" * 64,
        original_source_sha="4" * 40,
    )


def test_recovery_is_new_attempt_version_and_default_off() -> None:
    payload = _payload()
    assert payload["stage"] == "mayaki"
    assert payload["attempt_version"] == 2
    assert payload["state"] == "prepared"
    assert payload["enabled"] is False
    assert payload["target_source_sha"] == RECOVERY_TARGET_SOURCE_SHA
    assert payload["external_effects"] == 0
    assert len(canonical_digest(payload)) == 64


@pytest.mark.parametrize(
    "mutation",
    [
        {"enabled": True},
        {"state": "ready"},
        {"attempt_version": 1},
        {"original_request_digest": "5" * 64},
        {"target_source_sha": "f" * 40},
        {"external_effects": 1},
        {"unexpected": True},
    ],
)
def test_mutated_recovery_replay_fails_closed(
    mutation: dict[str, object],
) -> None:
    original = _payload()
    mutated = {**original, **mutation}
    with pytest.raises(ValueError):
        validate_recovery_replay(
            mutated,
            expected_digest=canonical_digest(original),
        )


def _attempt_payload() -> dict[str, object]:
    return {
        "schema": "gate.become-i4-recovery-attempt@2",
        "attempt_ref": "synthetic://recovery/attempt/" + "6" * 32,
        "attempt_version": 2,
        "correlation_ref": "synthetic://correlation/voice/" + "1" * 32,
        "prepared_recovery_digest": "2" * 64,
        "original_failed_request_digest": "3" * 64,
        "original_failed_receipt_digest": "4" * 64,
        "relations_receipt_digest": "5" * 64,
        "transcript_digest": "6" * 64,
        "contract_digest": (
            "91c3a210709c63738358bd0c512d11a9668acabd0997afcd68ba240bed38f573"
        ),
        "source_shas": {
            "mayaki": MAYAKI_SOURCE_SHA,
            "oko": OKO_SOURCE_SHA,
            "take": TAKE_SOURCE_SHA,
        },
        "stage_order": ["mayaki", "oko", "take"],
        "max_posts": 3,
        "state": "prepared",
        "external_effects": 0,
    }


def test_recovery_attempt_v2_is_exact_and_immutable() -> None:
    payload = _attempt_payload()
    assert validate_recovery_attempt_replay(
        payload,
        expected_digest=canonical_digest(payload),
    ) == payload


@pytest.mark.parametrize(
    "mutation",
    [
        {"attempt_version": 1},
        {"stage_order": ["oko", "mayaki", "take"]},
        {"max_posts": 4},
        {"source_shas": {
            "mayaki": MAYAKI_SOURCE_SHA,
            "oko": "f" * 40,
            "take": TAKE_SOURCE_SHA,
        }},
        {"transcript_digest": "7" * 64},
        {"external_effects": 1},
        {"unexpected": True},
    ],
)
def test_mutated_recovery_attempt_fails_closed(
    mutation: dict[str, object],
) -> None:
    original = _attempt_payload()
    mutated = {**original, **mutation}
    with pytest.raises(ValueError):
        validate_recovery_attempt_replay(
            mutated,
            expected_digest=canonical_digest(original),
        )


def test_recovery_stage_order_requires_reservation_and_completed_prefix() -> None:
    validate_recovery_event_sequence(
        [
            ("mayaki", 1, "reserved"),
            ("mayaki", 2, "completed"),
            ("oko", 1, "reserved"),
            ("oko", 2, "completed"),
            ("take", 1, "reserved"),
            ("take", 2, "completed"),
        ]
    )
    with pytest.raises(ValueError, match="stage_skipped"):
        validate_recovery_event_sequence([("oko", 1, "reserved")])
    with pytest.raises(ValueError, match="reservation_required"):
        validate_recovery_event_sequence([("mayaki", 2, "completed")])
    with pytest.raises(ValueError, match="terminal_blocks"):
        validate_recovery_event_sequence(
            [
                ("mayaki", 1, "reserved"),
                ("mayaki", 2, "failed"),
                ("oko", 1, "reserved"),
            ]
        )
