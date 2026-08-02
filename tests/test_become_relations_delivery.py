import hashlib
import json

import pytest
from app.application.become_controlled_intake import BecomeControlledIntakeError
from app.application.become_relations_delivery import (
    RELATIONS_CONSENT_DOMAIN_EVENT_SCHEMA,
    RELATIONS_CONSENT_EXECUTION_SCHEMA,
    parse_relations_consent_execution,
)


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution(**changes: object) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema": RELATIONS_CONSENT_EXECUTION_SCHEMA,
        "service": "relations",
        "source_sha": "f" * 40,
        "command_digest": "a" * 64,
        "idempotency_key": "idem_consent_fixture_001",
        "status": "executed",
        "next_event": "consent.granted",
        "execution_state": "executed",
        "domain_event_schema": RELATIONS_CONSENT_DOMAIN_EVENT_SCHEMA,
        "domain_event_id": "consent_evt_" + "b" * 24,
        "domain_event_digest": "c" * 64,
        "domain_events": 1,
        "external_effects": 0,
        "provider_calls": 0,
        "messages_or_publications": 0,
        "media_bytes": 0,
        "real_payments": 0,
        "contains_pii": False,
        **changes,
    }
    return {**unsigned, "receipt_digest": _digest(unsigned)}


def test_exact_provider_disabled_relations_execution_is_valid() -> None:
    execution = _execution()

    parsed = parse_relations_consent_execution(
        execution,
        expected_command_digest="a" * 64,
        expected_idempotency_key="idem_consent_fixture_001",
    )

    assert parsed == execution
    assert parsed["status"] == "executed"
    assert parsed["domain_events"] == 1
    assert parsed["external_effects"] == 0
    assert parsed["provider_calls"] == 0
    assert parsed["contains_pii"] is False
    assert "person_ref" not in parsed
    assert "correlation_ref" not in parsed


@pytest.mark.parametrize(
    "execution,code",
    [
        (_execution(provider_calls=1), "effect_forbidden"),
        (
            _execution(command_digest="b" * 64),
            "contract_invalid",
        ),
        (
            _execution(domain_events=0),
            "contract_invalid",
        ),
        (
            {**_execution(), "receipt_digest": "0" * 64},
            "digest_mismatch",
        ),
        (
            {**_execution(), "message_text": "private"},
            "shape_invalid",
        ),
    ],
)
def test_relations_execution_fails_closed(
    execution: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(BecomeControlledIntakeError, match=code):
        parse_relations_consent_execution(
            execution,
            expected_command_digest="a" * 64,
            expected_idempotency_key="idem_consent_fixture_001",
        )
