from __future__ import annotations

import json
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any

JsonObject = dict[str, Any]

CONTRACT_VERSION = "become.role-aware-downstream@1"
REQUEST_SCHEMA = "become.role-aware-downstream-request@1"
RECEIPT_SCHEMA = "become.role-aware-downstream-receipt@1"
CONTRACT_DIGEST = "085e0f49660b9374be4718824a624b207344334e1fe0182ee2256aa1cf57e2f9"

_OPERATION_BY_SERVICE = {
    "GATE": "GATE_VALIDATE_CHAIN",
    "TAKE": "TAKE_CREATE_PRIVATE_RESULT",
}


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ContractError(code)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode()).hexdigest()


def _exact_keys(value: JsonObject, expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _validate_artifact(artifact: JsonObject, expected_type: str) -> None:
    _exact_keys(
        artifact,
        {"artifact_type", "artifact_ref", "version", "content_digest"},
        "artifact_binding_invalid",
    )
    if artifact["artifact_type"] != expected_type:
        _fail("artifact_type_mismatch")
    if not isinstance(artifact["artifact_ref"], str) or not artifact["artifact_ref"].startswith(
        "artifact://"
    ):
        _fail("artifact_binding_invalid")
    if (
        not isinstance(artifact["version"], int)
        or artifact["version"] < 1
        or not _is_digest(artifact["content_digest"])
    ):
        _fail("artifact_binding_invalid")


def _same_artifact(left: JsonObject, right: JsonObject) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in ("artifact_type", "artifact_ref", "version", "content_digest")
    )


def _validate_approval(approval: JsonObject, request: JsonObject, artifact: JsonObject) -> None:
    _exact_keys(
        approval,
        {
            "approval_schema",
            "quest_ref",
            "approver_person_ref",
            "approver_role",
            "artifact",
            "approval_version",
            "approval_digest",
        },
        "human_approval_required",
    )
    if approval["approval_schema"] != "become.quest-approval@1":
        _fail("human_approval_required")
    if approval["quest_ref"] != request["quest_ref"]:
        _fail("quest_scope_mismatch")
    participants: JsonObject = request["participants"]
    expert: JsonObject = participants["expert"]
    if approval["approver_role"] != "EXPERT" or approval["approver_person_ref"] != expert[
        "person_ref"
    ]:
        _fail("approval_approver_mismatch")
    approval_artifact: JsonObject = approval["artifact"]
    if not _same_artifact(approval_artifact, artifact):
        _fail("approval_artifact_mismatch")
    _validate_artifact(approval_artifact, "EXERCISE")


def _validate_content_free(receipt: JsonObject) -> None:
    forbidden = {"content", "transcript", "voice", "telegram_user_id", "provider_id"}

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden:
                    _fail("public_receipt_not_content_free")
                visit(child)

    visit(receipt)
    if receipt["external_effects"] != 0:
        _fail("external_effects_forbidden")


class RoleAwareAdapter:
    production_routing_enabled = False
    durable_replay_claimed = False

    def __init__(self, service: str, service_source_sha: str) -> None:
        if service not in _OPERATION_BY_SERVICE:
            _fail("service_scope_mismatch")
        if re.fullmatch(r"[a-f0-9]{40}", service_source_sha) is None:
            _fail("service_source_sha_invalid")
        self._service = service
        self._service_source_sha = service_source_sha
        self._replays: dict[str, tuple[str, JsonObject]] = {}
        self._bindings: dict[str, tuple[str, str]] = {}

    def _validate_common(self, request: JsonObject, expected_scope: JsonObject | None) -> None:
        _exact_keys(
            request,
            {
                "schema",
                "contract_version",
                "contract_digest",
                "mode",
                "service",
                "quest_ref",
                "correlation_ref",
                "operation",
                "operation_ref",
                "attempt_version",
                "idempotency_key",
                "participants",
                "input",
            },
            "request_shape_invalid",
        )
        if (
            request["schema"] != REQUEST_SCHEMA
            or request["contract_version"] != CONTRACT_VERSION
            or request["contract_digest"] != CONTRACT_DIGEST
        ):
            _fail("contract_identity_mismatch")
        if request["service"] != self._service:
            _fail("service_scope_mismatch")
        if request["operation"] != _OPERATION_BY_SERVICE[self._service]:
            _fail("operation_not_allowed")
        participants: JsonObject = request["participants"]
        _exact_keys(participants, {"expert", "learner"}, "participant_binding_invalid")
        expert: JsonObject = participants["expert"]
        learner: JsonObject = participants["learner"]
        for binding, role in ((expert, "EXPERT"), (learner, "LEARNER")):
            _exact_keys(
                binding,
                {"role", "person_ref", "binding_version", "binding_digest"},
                "participant_binding_invalid",
            )
            if binding["role"] != role:
                _fail("participant_binding_invalid")
        if expert["person_ref"] == learner["person_ref"]:
            _fail("participants_not_distinct")
        if request["mode"] == "PRODUCTION" and (
            not expert["person_ref"].startswith("person://")
            or not learner["person_ref"].startswith("person://")
        ):
            _fail("synthetic_person_forbidden")
        if request["mode"] not in {"TEST", "PRODUCTION"}:
            _fail("mode_not_allowed")
        if expected_scope:
            for field, code in (
                ("quest_ref", "quest_scope_mismatch"),
                ("correlation_ref", "correlation_scope_mismatch"),
                ("operation_ref", "operation_scope_mismatch"),
                ("attempt_version", "attempt_scope_mismatch"),
            ):
                if field in expected_scope and request[field] != expected_scope[field]:
                    _fail(code)
        current = (expert["person_ref"], learner["person_ref"])
        prior = self._bindings.get(request["quest_ref"])
        if prior is not None and prior != current:
            _fail("participant_rebinding")
        self._bindings[request["quest_ref"]] = current

    def _validate_gate(self, request: JsonObject) -> None:
        gate_input: JsonObject = request["input"]
        _exact_keys(gate_input, {"chain_version", "validate_only"}, "gate_input_invalid")
        if gate_input != {"chain_version": CONTRACT_VERSION, "validate_only": True}:
            _fail("gate_default_off_required")

    def _validate_take(self, request: JsonObject) -> None:
        take_input: JsonObject = request["input"]
        _exact_keys(take_input, {"oko_result", "quest_approvals"}, "take_input_invalid")
        result: JsonObject = take_input["oko_result"]
        _exact_keys(
            result,
            {"quest_ref", "submission", "exercise", "result"},
            "exact_oko_result_required",
        )
        if result["quest_ref"] != request["quest_ref"]:
            _fail("quest_scope_mismatch")
        _validate_artifact(result["submission"], "SUBMISSION")
        _validate_artifact(result["exercise"], "EXERCISE")
        try:
            _validate_artifact(result["result"], "OKO_RESULT")
        except ContractError:
            _fail("exact_oko_result_required")
        approvals: list[JsonObject] = take_input["quest_approvals"]
        if not approvals:
            _fail("human_approval_required")
        for approval in approvals:
            _validate_approval(approval, request, result["exercise"])

    def execute(self, request: JsonObject, expected_scope: JsonObject | None = None) -> JsonObject:
        self._validate_common(request, expected_scope)
        request_digest = canonical_digest(request)
        replay_key = "|".join(
            str(request[field])
            for field in (
                "quest_ref",
                "correlation_ref",
                "operation_ref",
                "attempt_version",
                "idempotency_key",
            )
        )
        prior = self._replays.get(replay_key)
        if prior is not None:
            if prior[0] != request_digest:
                _fail("mutated_replay")
            return deepcopy(prior[1])
        if self._service == "GATE":
            self._validate_gate(request)
            outcome = "validated_default_off"
            output: JsonObject = {
                "validation": "accepted",
                "orchestration_enabled": False,
                "max_operations": 0,
            }
        else:
            self._validate_take(request)
            result: JsonObject = request["input"]["oko_result"]
            outcome = "private_result_created"
            suffix = str(request["operation_ref"]).removeprefix("operation://")
            output = {
                "result_state": "private_result_created",
                "private_result": {
                    "quest_ref": request["quest_ref"],
                    "oko_result": result["result"],
                    "result_artifact_ref": f"artifact://role-aware/{suffix}/private-free-result",
                    "version": 1,
                    "content_digest": canonical_digest(
                        {"requestDigest": request_digest, "type": "PRIVATE_FREE_RESULT"}
                    ),
                    "storage_classification": "PRIVATE",
                    "public_receipt_content_free": True,
                },
                "telegram_delivered": False,
                "published": False,
                "payment_created": False,
            }
        receipt: JsonObject = {
            "schema": RECEIPT_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "contract_digest": CONTRACT_DIGEST,
            "service": self._service,
            "quest_ref": request["quest_ref"],
            "correlation_ref": request["correlation_ref"],
            "operation": request["operation"],
            "operation_ref": request["operation_ref"],
            "attempt_version": request["attempt_version"],
            "idempotency_key": request["idempotency_key"],
            "request_digest": request_digest,
            "outcome": outcome,
            "service_source_sha": self._service_source_sha,
            "external_effects": 0,
            "output": output,
        }
        receipt["receipt_digest"] = canonical_digest(receipt)
        _validate_content_free(receipt)
        self._replays[replay_key] = (request_digest, receipt)
        return deepcopy(receipt)


def create_gate_adapter(service_source_sha: str) -> RoleAwareAdapter:
    return RoleAwareAdapter("GATE", service_source_sha)


def create_take_adapter(service_source_sha: str) -> RoleAwareAdapter:
    return RoleAwareAdapter("TAKE", service_source_sha)
