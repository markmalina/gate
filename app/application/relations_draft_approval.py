"""Default-off acceptance of proposed Relations draft-approval evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping

RELATIONS_MAIN_SHA = "3598833776c9fc660d518d712dceabc5e65582f5"
SOURCE_CONTRACT = "relations.draft-approval-recorded"
SOURCE_VERSION = "1.0.0"
ACCEPTANCE_CONTRACT = "relations.gate-draft-approval-acceptance@1"

_REF = re.compile(r"^[a-z][a-z_]*_[0-9A-Z]{10,26}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_EVENT_KEYS = frozenset(
    {"contract", "version", "canonicalStatus", "eventRef", "idempotencyKey",
     "occurredAt", "payload", "externalEffectsPerformed"}
)
_PAYLOAD_KEYS = frozenset(
    {"draftRef", "draftVersion", "personRef", "contentRef", "contentDigestSha256",
     "decision", "decidedByRef", "evidenceRef", "tenantRef", "purposeRef",
     "visibility", "deliveryAuthorized"}
)


class RelationsApprovalAcceptanceError(ValueError):
    """Fail-closed rejection at the Gate reference boundary."""


@dataclass(frozen=True, slots=True)
class RelationsApprovalAcceptancePolicy:
    enabled: bool = False


class RelationsApprovalReplayLedger:
    """Synthetic digest-only replay ledger; not wired to runtime."""

    def __init__(self) -> None:
        self._digests: dict[str, str] = {}

    def record(self, idempotency_key: str, acceptance: Mapping[str, object]) -> str:
        digest = hashlib.sha256(_stable_json(acceptance).encode()).hexdigest()
        previous = self._digests.get(idempotency_key)
        if previous is not None and previous != digest:
            raise RelationsApprovalAcceptanceError("relations_approval_replay_mutation")
        if previous is not None:
            return "replayed"
        self._digests[idempotency_key] = digest
        return "recorded"


def accept_relations_draft_approval(
    event: Mapping[str, object],
    *,
    policy: RelationsApprovalAcceptancePolicy | None = None,
) -> dict[str, object]:
    """Validate one synthetic event and return non-authoritative reference evidence."""
    if not (policy or RelationsApprovalAcceptancePolicy()).enabled:
        raise RelationsApprovalAcceptanceError("relations_approval_acceptance_disabled")
    _exact_keys(event, _EVENT_KEYS, "event")
    if event["contract"] != SOURCE_CONTRACT or event["version"] != SOURCE_VERSION:
        raise RelationsApprovalAcceptanceError("relations_approval_contract_mismatch")
    if event["canonicalStatus"] != "proposed-local":
        raise RelationsApprovalAcceptanceError("relations_approval_not_proposed_local")
    if event["externalEffectsPerformed"] is not False:
        raise RelationsApprovalAcceptanceError("relations_approval_external_effect_claim")
    _opaque(event["eventRef"], "event_")
    _opaque(event["idempotencyKey"], "idempotency_")

    payload = event["payload"]
    if not isinstance(payload, Mapping):
        raise RelationsApprovalAcceptanceError("relations_approval_payload_invalid")
    _exact_keys(payload, _PAYLOAD_KEYS, "payload")
    for key, prefix in (
        ("draftRef", "draft_"), ("personRef", "person_"), ("contentRef", "content_"),
        ("decidedByRef", "person_"), ("evidenceRef", "evidence_"),
        ("tenantRef", "tenant_"), ("purposeRef", "purpose_"),
    ):
        _opaque(payload[key], prefix)
    version = payload["draftVersion"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RelationsApprovalAcceptanceError("relations_approval_version_invalid")
    digest = payload["contentDigestSha256"]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise RelationsApprovalAcceptanceError("relations_approval_digest_invalid")
    if payload["decision"] not in {"approved", "rejected"}:
        raise RelationsApprovalAcceptanceError("relations_approval_decision_invalid")
    if payload["visibility"] not in {"private", "family", "commercial", "program"}:
        raise RelationsApprovalAcceptanceError("relations_approval_visibility_invalid")
    if payload["deliveryAuthorized"] is not False:
        raise RelationsApprovalAcceptanceError("relations_approval_delivery_authority_forbidden")

    return {
        "contract": ACCEPTANCE_CONTRACT,
        "canonicalStatus": "proposed-local",
        "sourceCommit": RELATIONS_MAIN_SHA,
        "sourceEventRef": payload and event["eventRef"],
        "draftRef": payload["draftRef"],
        "draftVersion": version,
        "personRef": payload["personRef"],
        "contentRef": payload["contentRef"],
        "contentDigestSha256": digest,
        "approvalDecision": payload["decision"],
        "acceptedFor": "reviewed-draft-reference-only",
        "deliveryAuthorized": False,
        "providerRef": None,
        "deliveryRef": None,
        "externalEffectsPerformed": False,
    }


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    if frozenset(value) != expected:
        raise RelationsApprovalAcceptanceError(f"relations_approval_{label}_shape_invalid")


def _opaque(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or _REF.fullmatch(value) is None:
        raise RelationsApprovalAcceptanceError("relations_approval_reference_invalid")
    return value


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
