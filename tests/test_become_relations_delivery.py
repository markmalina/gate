import hashlib
import json

import pytest
from app.application.become_controlled_intake import BecomeControlledIntakeError
from app.application.become_relations_delivery import (
    RELATIONS_CONSENT_ACCEPTANCE_SCHEMA,
    parse_relations_consent_acceptance,
)


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _acceptance(**changes: object) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema": RELATIONS_CONSENT_ACCEPTANCE_SCHEMA,
        "service": "relations",
        "source_sha": "f" * 40,
        "command_digest": "a" * 64,
        "idempotency_key": "idem_consent_fixture_001",
        "status": "accepted_not_executed",
        "next_event": "consent.granted",
        "execution_state": "accepted_not_executed",
        "external_effects": 0,
        "database_writes": 0,
        "provider_calls": 0,
        "messages_or_publications": 0,
        "media_bytes": 0,
        "real_payments": 0,
        "contains_pii": False,
        **changes,
    }
    return {**unsigned, "receipt_digest": _digest(unsigned)}


def test_exact_provider_disabled_relations_acceptance_is_valid() -> None:
    acceptance = _acceptance()

    parsed = parse_relations_consent_acceptance(
        acceptance,
        expected_command_digest="a" * 64,
        expected_idempotency_key="idem_consent_fixture_001",
    )

    assert parsed == acceptance
    assert parsed["status"] == "accepted_not_executed"
    assert parsed["external_effects"] == 0
    assert parsed["database_writes"] == 0
    assert parsed["provider_calls"] == 0
    assert parsed["contains_pii"] is False


@pytest.mark.parametrize(
    "acceptance,code",
    [
        (_acceptance(provider_calls=1), "effect_forbidden"),
        (
            _acceptance(command_digest="b" * 64),
            "contract_invalid",
        ),
        (
            {**_acceptance(), "receipt_digest": "0" * 64},
            "digest_mismatch",
        ),
        (
            {**_acceptance(), "message_text": "private"},
            "shape_invalid",
        ),
    ],
)
def test_relations_acceptance_fails_closed(
    acceptance: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(BecomeControlledIntakeError, match=code):
        parse_relations_consent_acceptance(
            acceptance,
            expected_command_digest="a" * 64,
            expected_idempotency_key="idem_consent_fixture_001",
        )
