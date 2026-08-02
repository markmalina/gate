"""Content-free contracts for the provider-disabled BECOME I4 chain."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

I4_CONTRACT_DIGEST = "91c3a210709c63738358bd0c512d11a9668acabd0997afcd68ba240bed38f573"
I4_REQUEST_SCHEMA = "become.i4-service-request@1"
I4_RECEIPT_SCHEMA = "become.i4-service-receipt@1"
I4_MODE = "SIMULATED"

_REF = re.compile(r"^synthetic://[a-z0-9/_-]+$")
_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "contract_digest",
        "mode",
        "quest_ref",
        "correlation_ref",
        "subject_ref",
        "input",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "service",
        "source_sha",
        "contract_digest",
        "mode",
        "quest_ref",
        "correlation_ref",
        "subject_ref",
        "external_effects",
        "output",
        "receipt_digest",
    }
)
_RELATIONS_INPUT_FIELDS = frozenset(
    {"alias_refs", "consent_receipt_ref", "referral_edge_ref"}
)
_RELATIONS_OUTPUT_FIELDS = frozenset(
    {
        "person_ref",
        "alias_count",
        "person_count",
        "consent_receipt_ref",
        "eligibility_receipt_ref",
        "referral_edge_ref",
        "attribution_scope",
    }
)
_MAYAKI_INPUT_FIELDS = frozenset(
    {"eligibility_receipt_ref", "requested_product_ref"}
)
_MAYAKI_OUTPUT_FIELDS = frozenset(
    {
        "profile_version_ref",
        "product_version_ref",
        "program_version_ref",
        "exercise_version_ref",
        "approval_receipt_ref",
        "enrollment_ref",
        "entitlement_ref",
        "immutable_version_graph",
    }
)
_OKO_INPUT_FIELDS = frozenset(
    {
        "enrollment_ref",
        "entitlement_ref",
        "exercise_version_ref",
        "duration_limit_seconds",
    }
)
_OKO_OUTPUT_FIELDS = frozenset(
    {
        "exercise_session_ref",
        "submission_ref",
        "result_receipt_ref",
        "duration_seconds",
        "free_result_delivered",
        "restart_safe",
        "duplicate_effects",
    }
)
_TAKE_INPUT_FIELDS = frozenset(
    {"consent_receipt_ref", "source_asset_ref", "approval_receipt_ref"}
)
_TAKE_OUTPUT_FIELDS = frozenset(
    {
        "transcript_version_ref",
        "render_ref",
        "publication_ref",
        "exact_approval",
        "byte_count",
        "publication_state",
        "privacy_closure_receipt_ref",
    }
)

JsonObject = dict[str, Any]


class BecomeI4ContractError(ValueError):
    """Fail-closed I4 request or receipt contract error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BecomeI4Stage(StrEnum):
    RELATIONS = "relations"
    MAYAKI = "mayaki"
    OKO = "oko"
    TAKE = "take"


@dataclass(frozen=True, slots=True)
class BecomeI4CorrelationEvidence:
    """Content-free Gate evidence used to project one exact I4 identity."""

    correlation_ref: str
    consent_quest_ref: str
    gate_subject_ref: str
    voice_intake_digest: str
    transcript_digest: str
    consent_receipt_digest: str
    relations_execution_digest: str
    relations_domain_event_id: str
    relations_domain_event_digest: str

    def __post_init__(self) -> None:
        _require_ref(self.correlation_ref, "correlation_ref")
        if not self.correlation_ref.startswith("synthetic://correlation/voice/"):
            raise BecomeI4ContractError("voice_correlation_ref_required")
        if not self.consent_quest_ref or not self.gate_subject_ref:
            raise BecomeI4ContractError("gate_correlation_identity_required")
        for field, value in (
            ("voice_intake_digest", self.voice_intake_digest),
            ("transcript_digest", self.transcript_digest),
            ("consent_receipt_digest", self.consent_receipt_digest),
            ("relations_execution_digest", self.relations_execution_digest),
            ("relations_domain_event_digest", self.relations_domain_event_digest),
        ):
            _require_digest(value, field)
        if not re.fullmatch(r"consent_evt_[a-f0-9]{24}", self.relations_domain_event_id):
            raise BecomeI4ContractError("relations_domain_event_id_invalid")


@dataclass(frozen=True, slots=True)
class BecomeI4ProjectedRefs:
    quest_ref: str
    subject_ref: str
    voice_alias_ref: str
    consent_receipt_ref: str
    referral_edge_ref: str
    requested_product_ref: str


@dataclass(frozen=True, slots=True)
class BuiltTakeRequest:
    request: JsonObject
    source_asset_ref: str
    mapping_digest: str


def project_i4_refs(evidence: BecomeI4CorrelationEvidence) -> BecomeI4ProjectedRefs:
    """Project real Gate correlation into opaque refs accepted by frozen I4 services."""

    quest_seed = {
        "schema": "gate.become-i4-quest-projection@1",
        "consent_quest_ref": evidence.consent_quest_ref,
        "correlation_ref": evidence.correlation_ref,
        "consent_receipt_digest": evidence.consent_receipt_digest,
        "relations_execution_digest": evidence.relations_execution_digest,
    }
    consent_seed = {
        "schema": "gate.become-i4-consent-projection@1",
        "consent_receipt_digest": evidence.consent_receipt_digest,
        "relations_execution_digest": evidence.relations_execution_digest,
        "domain_event_id": evidence.relations_domain_event_id,
        "domain_event_digest": evidence.relations_domain_event_digest,
    }
    quest_ref = _project_ref("quest/become-voice", quest_seed)
    return BecomeI4ProjectedRefs(
        quest_ref=quest_ref,
        subject_ref=_project_ref("alias/telegram", evidence.gate_subject_ref),
        voice_alias_ref=_project_ref("alias/voice", evidence.correlation_ref),
        consent_receipt_ref=_project_ref("gate/consent", consent_seed),
        referral_edge_ref=_project_ref(
            "relations/referral-edge",
            {
                "correlation_ref": evidence.correlation_ref,
                "relations_execution_digest": evidence.relations_execution_digest,
            },
        ),
        requested_product_ref=_project_ref(
            "product/become-free-voice",
            {"quest_ref": quest_ref, "contract_digest": I4_CONTRACT_DIGEST},
        ),
    )


def build_relations_request(
    evidence: BecomeI4CorrelationEvidence,
    refs: BecomeI4ProjectedRefs,
) -> JsonObject:
    return _request(
        evidence=evidence,
        refs=refs,
        subject_ref=refs.subject_ref,
        domain_input={
            "alias_refs": [refs.subject_ref, refs.voice_alias_ref],
            "consent_receipt_ref": refs.consent_receipt_ref,
            "referral_edge_ref": refs.referral_edge_ref,
        },
        expected_input_fields=_RELATIONS_INPUT_FIELDS,
    )


def build_mayaki_request(
    evidence: BecomeI4CorrelationEvidence,
    refs: BecomeI4ProjectedRefs,
    relations_receipt: Mapping[str, object],
) -> JsonObject:
    output = _receipt_output(relations_receipt, BecomeI4Stage.RELATIONS)
    return _request(
        evidence=evidence,
        refs=refs,
        subject_ref=_require_ref(output.get("person_ref"), "relations.person_ref"),
        domain_input={
            "eligibility_receipt_ref": _require_ref(
                output.get("eligibility_receipt_ref"),
                "relations.eligibility_receipt_ref",
            ),
            "requested_product_ref": refs.requested_product_ref,
        },
        expected_input_fields=_MAYAKI_INPUT_FIELDS,
    )


def build_oko_request(
    evidence: BecomeI4CorrelationEvidence,
    refs: BecomeI4ProjectedRefs,
    relations_receipt: Mapping[str, object],
    mayaki_receipt: Mapping[str, object],
) -> JsonObject:
    relations = _receipt_output(relations_receipt, BecomeI4Stage.RELATIONS)
    mayaki = _receipt_output(mayaki_receipt, BecomeI4Stage.MAYAKI)
    return _request(
        evidence=evidence,
        refs=refs,
        subject_ref=_require_ref(relations.get("person_ref"), "relations.person_ref"),
        domain_input={
            "enrollment_ref": _require_ref(
                mayaki.get("enrollment_ref"), "mayaki.enrollment_ref"
            ),
            "entitlement_ref": _require_ref(
                mayaki.get("entitlement_ref"), "mayaki.entitlement_ref"
            ),
            "exercise_version_ref": _require_ref(
                mayaki.get("exercise_version_ref"),
                "mayaki.exercise_version_ref",
            ),
            "duration_limit_seconds": 600,
        },
        expected_input_fields=_OKO_INPUT_FIELDS,
    )


def build_take_request(
    evidence: BecomeI4CorrelationEvidence,
    refs: BecomeI4ProjectedRefs,
    relations_receipt: Mapping[str, object],
    mayaki_receipt: Mapping[str, object],
    oko_receipt: Mapping[str, object],
) -> BuiltTakeRequest:
    relations = _receipt_output(relations_receipt, BecomeI4Stage.RELATIONS)
    mayaki = _receipt_output(mayaki_receipt, BecomeI4Stage.MAYAKI)
    oko = _receipt_output(oko_receipt, BecomeI4Stage.OKO)
    oko_result_ref = _require_ref(
        oko.get("result_receipt_ref"), "oko.result_receipt_ref"
    )
    source_asset_ref = _project_ref(
        "gate/source",
        {
            "schema": "gate.become-i4-take-source@1",
            "correlation_ref": evidence.correlation_ref,
            "voice_intake_digest": evidence.voice_intake_digest,
            "transcript_digest": evidence.transcript_digest,
            "oko_result_receipt_ref": oko_result_ref,
        },
    )
    request = _request(
        evidence=evidence,
        refs=refs,
        subject_ref=_require_ref(relations.get("person_ref"), "relations.person_ref"),
        domain_input={
            "consent_receipt_ref": _require_ref(
                relations.get("consent_receipt_ref"),
                "relations.consent_receipt_ref",
            ),
            "source_asset_ref": source_asset_ref,
            "approval_receipt_ref": _require_ref(
                mayaki.get("approval_receipt_ref"),
                "mayaki.approval_receipt_ref",
            ),
        },
        expected_input_fields=_TAKE_INPUT_FIELDS,
    )
    return BuiltTakeRequest(
        request=request,
        source_asset_ref=source_asset_ref,
        mapping_digest=take_mapping_digest(
            oko_result_receipt_ref=oko_result_ref,
            take_source_asset_ref=source_asset_ref,
        ),
    )


def validate_i4_receipt(
    *,
    stage: BecomeI4Stage,
    receipt: object,
    request: Mapping[str, object],
    expected_source_sha: str,
) -> JsonObject:
    """Validate one exact common receipt plus all service-specific invariants."""

    _require_source_sha(expected_source_sha)
    _validate_request_envelope(request)
    value = _object(receipt, "service_receipt_object_required")
    _exact_fields(value, _RECEIPT_FIELDS, "service_receipt_fields_invalid")
    if (
        value.get("schema") != I4_RECEIPT_SCHEMA
        or value.get("service") != stage.value
        or value.get("source_sha") != expected_source_sha
        or value.get("contract_digest") != I4_CONTRACT_DIGEST
        or value.get("mode") != request.get("mode")
        or value.get("quest_ref") != request.get("quest_ref")
        or value.get("correlation_ref") != request.get("correlation_ref")
        or value.get("subject_ref") != request.get("subject_ref")
        or value.get("external_effects") != 0
    ):
        raise BecomeI4ContractError("service_receipt_binding_invalid")
    receipt_digest = value.get("receipt_digest")
    _require_digest(receipt_digest, "receipt_digest")
    unsigned = dict(value)
    del unsigned["receipt_digest"]
    if canonical_digest(unsigned) != receipt_digest:
        raise BecomeI4ContractError("service_receipt_digest_invalid")
    output = _object(value.get("output"), "service_output_object_required")
    domain_input = _object(request.get("input"), "request_input_object_required")
    if stage is BecomeI4Stage.RELATIONS:
        _validate_relations_output(output, domain_input)
    elif stage is BecomeI4Stage.MAYAKI:
        _validate_mayaki_output(output)
    elif stage is BecomeI4Stage.OKO:
        _validate_oko_output(output)
    else:
        _validate_take_output(output)
    return dict(value)


def take_mapping_digest(
    *,
    oko_result_receipt_ref: str,
    take_source_asset_ref: str,
) -> str:
    _require_ref(oko_result_receipt_ref, "oko_result_receipt_ref")
    _require_ref(take_source_asset_ref, "take_source_asset_ref")
    return canonical_digest(
        {
            "oko_result_receipt_ref": oko_result_receipt_ref,
            "take_source_asset_ref": take_source_asset_ref,
        }
    )


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request(
    *,
    evidence: BecomeI4CorrelationEvidence,
    refs: BecomeI4ProjectedRefs,
    subject_ref: str,
    domain_input: JsonObject,
    expected_input_fields: frozenset[str],
) -> JsonObject:
    _require_ref(subject_ref, "subject_ref")
    _exact_fields(domain_input, expected_input_fields, "request_input_fields_invalid")
    request: JsonObject = {
        "schema": I4_REQUEST_SCHEMA,
        "contract_digest": I4_CONTRACT_DIGEST,
        "mode": I4_MODE,
        "quest_ref": refs.quest_ref,
        "correlation_ref": evidence.correlation_ref,
        "subject_ref": subject_ref,
        "input": domain_input,
    }
    _validate_request_envelope(request)
    return request


def _validate_request_envelope(request: Mapping[str, object]) -> None:
    _exact_fields(request, _REQUEST_FIELDS, "request_fields_invalid")
    if (
        request.get("schema") != I4_REQUEST_SCHEMA
        or request.get("contract_digest") != I4_CONTRACT_DIGEST
        or request.get("mode") != I4_MODE
    ):
        raise BecomeI4ContractError("request_contract_invalid")
    for field in ("quest_ref", "correlation_ref", "subject_ref"):
        _require_ref(request.get(field), field)


def _validate_relations_output(
    output: Mapping[str, object], domain_input: Mapping[str, object]
) -> None:
    _exact_fields(output, _RELATIONS_OUTPUT_FIELDS, "relations_output_fields_invalid")
    for field in (
        "person_ref",
        "consent_receipt_ref",
        "eligibility_receipt_ref",
        "referral_edge_ref",
    ):
        _require_ref(output.get(field), f"relations.{field}")
    if (
        output.get("alias_count") != 2
        or output.get("person_count") != 1
        or output.get("attribution_scope") != "PERSON"
        or output.get("consent_receipt_ref") != domain_input.get("consent_receipt_ref")
        or output.get("referral_edge_ref") != domain_input.get("referral_edge_ref")
    ):
        raise BecomeI4ContractError("relations_output_invariant_invalid")


def _validate_mayaki_output(output: Mapping[str, object]) -> None:
    _exact_fields(output, _MAYAKI_OUTPUT_FIELDS, "mayaki_output_fields_invalid")
    for field in _MAYAKI_OUTPUT_FIELDS - {"immutable_version_graph"}:
        _require_ref(output.get(field), f"mayaki.{field}")
    if output.get("immutable_version_graph") is not True:
        raise BecomeI4ContractError("mayaki_output_invariant_invalid")


def _validate_oko_output(output: Mapping[str, object]) -> None:
    _exact_fields(output, _OKO_OUTPUT_FIELDS, "oko_output_fields_invalid")
    for field in ("exercise_session_ref", "submission_ref", "result_receipt_ref"):
        _require_ref(output.get(field), f"oko.{field}")
    if (
        type(output.get("duration_seconds")) is not int
        or output.get("duration_seconds") != 600
        or output.get("free_result_delivered") is not True
        or output.get("restart_safe") is not True
        or output.get("duplicate_effects") != 0
    ):
        raise BecomeI4ContractError("oko_output_invariant_invalid")


def _validate_take_output(output: Mapping[str, object]) -> None:
    _exact_fields(output, _TAKE_OUTPUT_FIELDS, "take_output_fields_invalid")
    for field in (
        "transcript_version_ref",
        "render_ref",
        "publication_ref",
        "privacy_closure_receipt_ref",
    ):
        _require_ref(output.get(field), f"take.{field}")
    if (
        output.get("exact_approval") is not True
        or output.get("byte_count") != 0
        or output.get("publication_state") != "disabled"
    ):
        raise BecomeI4ContractError("take_output_invariant_invalid")


def _receipt_output(
    receipt: Mapping[str, object], stage: BecomeI4Stage
) -> Mapping[str, object]:
    if receipt.get("service") != stage.value:
        raise BecomeI4ContractError(f"{stage.value}_receipt_required")
    return _object(receipt.get("output"), f"{stage.value}_output_required")


def _project_ref(kind: str, value: object) -> str:
    ref = f"synthetic://{kind}/{canonical_digest(value)[:32]}"
    return _require_ref(ref, kind)


def _object(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise BecomeI4ContractError(code)
    return value


def _exact_fields(
    value: Mapping[str, object], expected: frozenset[str], code: str
) -> None:
    if set(value) != expected:
        raise BecomeI4ContractError(code)


def _require_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or _REF.fullmatch(value) is None:
        raise BecomeI4ContractError(f"{field}_invalid")
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise BecomeI4ContractError(f"{field}_invalid")
    return value


def _require_source_sha(value: object) -> str:
    if not isinstance(value, str) or _SHA40.fullmatch(value) is None:
        raise BecomeI4ContractError("source_sha_invalid")
    return value
