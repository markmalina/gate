"""Exact metadata-only Relations consent execution receipt contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.become_controlled_intake import BecomeControlledIntakeError

JsonObject = dict[str, Any]

RELATIONS_CONSENT_EXECUTION_SCHEMA = (
    "relations.controlled-consent-execution@1"
)
RELATIONS_CONSENT_DOMAIN_EVENT_SCHEMA = "relations.consent-domain-event@1"
_EXECUTION_KEYS = frozenset(
    {
        "schema",
        "service",
        "source_sha",
        "command_digest",
        "idempotency_key",
        "status",
        "next_event",
        "execution_state",
        "domain_event_schema",
        "domain_event_id",
        "domain_event_digest",
        "domain_events",
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
        "contains_pii",
        "receipt_digest",
    }
)


def parse_relations_consent_execution(
    value: object,
    *,
    expected_command_digest: str,
    expected_idempotency_key: str,
) -> JsonObject:
    """Accept only exact executed proof bound to the immutable command."""

    if not isinstance(value, dict) or set(value) != _EXECUTION_KEYS:
        raise BecomeControlledIntakeError(
            "become_relations_execution_shape_invalid"
        )
    if (
        value.get("schema") != RELATIONS_CONSENT_EXECUTION_SCHEMA
        or value.get("service") != "relations"
        or value.get("command_digest") != expected_command_digest
        or value.get("idempotency_key") != expected_idempotency_key
        or value.get("status") != "executed"
        or value.get("next_event") != "consent.granted"
        or value.get("execution_state") != "executed"
        or value.get("domain_event_schema")
        != RELATIONS_CONSENT_DOMAIN_EVENT_SCHEMA
        or value.get("domain_events") != 1
        or value.get("contains_pii") is not False
    ):
        raise BecomeControlledIntakeError(
            "become_relations_execution_contract_invalid"
        )
    source_sha = value.get("source_sha")
    domain_event_id = value.get("domain_event_id")
    domain_event_digest = value.get("domain_event_digest")
    receipt_digest = value.get("receipt_digest")
    if (
        not _is_lower_hex(source_sha, 40)
        or not isinstance(domain_event_id, str)
        or not domain_event_id.startswith("consent_evt_")
        or not _is_lower_hex(domain_event_id.removeprefix("consent_evt_"), 24)
        or not _is_lower_hex(domain_event_digest, 64)
        or not _is_lower_hex(receipt_digest, 64)
    ):
        raise BecomeControlledIntakeError(
            "become_relations_execution_digest_invalid"
        )
    for key in (
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
    ):
        if value.get(key) != 0:
            raise BecomeControlledIntakeError(
                f"become_relations_execution_effect_forbidden:{key}"
            )
    unsigned = dict(value)
    del unsigned["receipt_digest"]
    if _digest_object(unsigned) != receipt_digest:
        raise BecomeControlledIntakeError(
            "become_relations_execution_digest_mismatch"
        )
    return dict(value)


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _digest_object(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
