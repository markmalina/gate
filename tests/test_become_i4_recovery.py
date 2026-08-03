from __future__ import annotations

import pytest

from app.application.become_i4_chain import canonical_digest
from app.infrastructure.become_i4_recovery import (
    RECOVERY_TARGET_SOURCE_SHA,
    build_recovery_payload,
    validate_recovery_payload,
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
    if mutation == {"original_request_digest": "5" * 64}:
        # A changed digest can be structurally valid, but its replay digest is not.
        assert canonical_digest(mutated) != canonical_digest(original)
        return
    with pytest.raises(ValueError):
        validate_recovery_payload(mutated)
