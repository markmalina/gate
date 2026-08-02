"""Exact provider-disabled Relations acceptance receipt contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.become_controlled_intake import BecomeControlledIntakeError

JsonObject = dict[str, Any]

RELATIONS_CONSENT_ACCEPTANCE_SCHEMA = (
    "relations.controlled-consent-acceptance@1"
)
_ACCEPTANCE_KEYS = frozenset(
    {
        "schema",
        "service",
        "source_sha",
        "command_digest",
        "idempotency_key",
        "status",
        "next_event",
        "execution_state",
        "external_effects",
        "database_writes",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
        "contains_pii",
        "receipt_digest",
    }
)


def parse_relations_consent_acceptance(
    value: object,
    *,
    expected_command_digest: str,
    expected_idempotency_key: str,
) -> JsonObject:
    """Accept only a receipt bound to the exact command with zero effects."""

    if not isinstance(value, dict) or set(value) != _ACCEPTANCE_KEYS:
        raise BecomeControlledIntakeError(
            "become_relations_acceptance_shape_invalid"
        )
    if (
        value.get("schema") != RELATIONS_CONSENT_ACCEPTANCE_SCHEMA
        or value.get("service") != "relations"
        or value.get("command_digest") != expected_command_digest
        or value.get("idempotency_key") != expected_idempotency_key
        or value.get("status") != "accepted_not_executed"
        or value.get("next_event") != "consent.granted"
        or value.get("execution_state") != "accepted_not_executed"
        or value.get("contains_pii") is not False
    ):
        raise BecomeControlledIntakeError(
            "become_relations_acceptance_contract_invalid"
        )
    source_sha = value.get("source_sha")
    receipt_digest = value.get("receipt_digest")
    if (
        not isinstance(source_sha, str)
        or len(source_sha) != 40
        or any(character not in "0123456789abcdef" for character in source_sha)
        or not isinstance(receipt_digest, str)
        or len(receipt_digest) != 64
    ):
        raise BecomeControlledIntakeError(
            "become_relations_acceptance_digest_invalid"
        )
    for key in (
        "external_effects",
        "database_writes",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
    ):
        if value.get(key) != 0:
            raise BecomeControlledIntakeError(
                f"become_relations_acceptance_effect_forbidden:{key}"
            )
    unsigned = dict(value)
    del unsigned["receipt_digest"]
    if _digest_object(unsigned) != receipt_digest:
        raise BecomeControlledIntakeError(
            "become_relations_acceptance_digest_mismatch"
        )
    return dict(value)


def _digest_object(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
