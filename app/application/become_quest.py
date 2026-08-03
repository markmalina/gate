"""Fail-closed role-correct BECOME Quest contracts and transitions."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

QUEST_CONTRACT_VERSION = "become.role-correct-quest@1"
APPROVAL_SCHEMA = "become.quest-approval@1"
QUEST_PROJECTION_ENABLED = False
QUEST_MAX_OPERATIONS = 0

SENSITIVE_FIELDS = frozenset(
    {
        "raw_voice",
        "transcript",
        "transcript_text",
        "telegram_id",
        "telegram_user_id",
        "telegram_chat_id",
        "telegram_username",
        "provider_operation_id",
        "product_text",
        "exercise_text",
    }
)


class QuestState(StrEnum):
    DRAFT = "DRAFT"
    EXPERT_BOUND = "EXPERT_BOUND"
    EXPERT_CONSENTED = "EXPERT_CONSENTED"
    TRANSCRIPT_PENDING_REVIEW = "TRANSCRIPT_PENDING_REVIEW"
    TRANSCRIPT_APPROVED = "TRANSCRIPT_APPROVED"
    PRODUCT_PENDING_APPROVAL = "PRODUCT_PENDING_APPROVAL"
    PRODUCT_APPROVED = "PRODUCT_APPROVED"
    LEARNER_INVITED = "LEARNER_INVITED"
    LEARNER_ENROLLED = "LEARNER_ENROLLED"
    READY_FOR_EXERCISE = "READY_FOR_EXERCISE"
    BLOCKED = "BLOCKED"
    TERMINAL = "TERMINAL"


class QuestRole(StrEnum):
    EXPERT = "EXPERT"
    LEARNER = "LEARNER"


class ArtifactType(StrEnum):
    TRANSCRIPT = "TRANSCRIPT"
    EXPERT_PROFILE = "EXPERT_PROFILE"
    PRODUCT = "PRODUCT"
    PROGRAM = "PROGRAM"
    EXERCISE = "EXERCISE"


class BecomeQuestError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


JsonObject = dict[str, object]


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_content_free(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SENSITIVE_FIELDS:
                raise BecomeQuestError("sensitive_field_forbidden")
            _assert_content_free(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_content_free(nested)


def _require_ref(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith("ref://"):
        raise BecomeQuestError(f"{field_name}_invalid")
    if len(value) < 8 or len(value) > 160:
        raise BecomeQuestError(f"{field_name}_invalid")
    return value


def _require_digest(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise BecomeQuestError(f"{field_name}_invalid")
    return value


def operation_attempt_key(
    *,
    quest_ref: str,
    correlation_ref: str,
    operation_type: str,
    attempt_version: int,
) -> str:
    _require_ref(quest_ref, "quest_ref")
    _require_ref(correlation_ref, "correlation_ref")
    if not operation_type or attempt_version < 1:
        raise BecomeQuestError("attempt_scope_invalid")
    return "quest-attempt:" + canonical_digest(
        {
            "quest_ref": quest_ref,
            "correlation_ref": correlation_ref,
            "operation_type": operation_type,
            "attempt_version": attempt_version,
        }
    )


def build_approval_receipt(
    *,
    quest_ref: str,
    artifact_type: ArtifactType,
    artifact_version: int,
    artifact_digest: str,
    approver_person_ref: str,
    approved_at: datetime,
) -> JsonObject:
    _require_ref(quest_ref, "quest_ref")
    _require_ref(approver_person_ref, "approver_person_ref")
    _require_digest(artifact_digest, "artifact_digest")
    if artifact_version < 1:
        raise BecomeQuestError("artifact_version_invalid")
    receipt: JsonObject = {
        "schema": APPROVAL_SCHEMA,
        "quest_id": quest_ref,
        "artifact_type": artifact_type.value,
        "artifact_version": artifact_version,
        "artifact_digest": artifact_digest,
        "approver_person_ref": approver_person_ref,
        "approver_role": QuestRole.EXPERT.value,
        "approval_state": "APPROVED",
        "approved_at": approved_at.isoformat(),
        "contract_version": QUEST_CONTRACT_VERSION,
        "external_effects": 0,
    }
    _assert_content_free(receipt)
    return receipt


@dataclass(slots=True)
class QuestSnapshot:
    quest_ref: str
    correlation_ref: str
    mode: str
    state: QuestState = QuestState.DRAFT
    expert_person_ref: str | None = None
    learner_person_ref: str | None = None
    expert_consented: bool = False
    learner_enrolled: bool = False
    artifacts: dict[str, tuple[int, str]] = field(default_factory=dict)
    approvals: dict[str, tuple[int, str]] = field(default_factory=dict)


class BecomeQuestStore(Protocol):
    def load(self, quest_ref: str) -> QuestSnapshot | None: ...

    def save(self, snapshot: QuestSnapshot) -> None: ...

    def replay(self, idempotency_key: str) -> tuple[str, JsonObject] | None: ...

    def save_replay(
        self,
        idempotency_key: str,
        request_digest: str,
        receipt: JsonObject,
    ) -> None: ...


class SnapshotQuestStore:
    """Test store whose snapshot models a durable process restart."""

    def __init__(self, snapshot: dict[str, object] | None = None) -> None:
        raw = copy.deepcopy(snapshot or {"quests": {}, "replays": {}})
        self.quests: dict[str, QuestSnapshot] = raw["quests"]  # type: ignore[assignment]
        self.replays: dict[str, tuple[str, JsonObject]] = raw["replays"]  # type: ignore[assignment]

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy({"quests": self.quests, "replays": self.replays})

    def load(self, quest_ref: str) -> QuestSnapshot | None:
        value = self.quests.get(quest_ref)
        return copy.deepcopy(value)

    def save(self, snapshot: QuestSnapshot) -> None:
        self.quests[snapshot.quest_ref] = copy.deepcopy(snapshot)

    def replay(self, idempotency_key: str) -> tuple[str, JsonObject] | None:
        value = self.replays.get(idempotency_key)
        return copy.deepcopy(value)

    def save_replay(
        self,
        idempotency_key: str,
        request_digest: str,
        receipt: JsonObject,
    ) -> None:
        self.replays[idempotency_key] = (request_digest, copy.deepcopy(receipt))


class BecomeQuestCoordinator:
    def __init__(self, store: BecomeQuestStore) -> None:
        self.store = store

    def execute(self, command: JsonObject) -> JsonObject:
        _assert_content_free(command)
        request_digest = canonical_digest(command)
        key = command.get("idempotency_key")
        if not isinstance(key, str) or not key:
            raise BecomeQuestError("idempotency_key_required")
        prior = self.store.replay(key)
        if prior is not None:
            prior_digest, receipt = prior
            if prior_digest != request_digest:
                raise BecomeQuestError("mutated_replay")
            return receipt
        receipt = self._execute_new(command)
        self.store.save_replay(key, request_digest, receipt)
        return receipt

    def _execute_new(self, command: JsonObject) -> JsonObject:
        quest_ref = _require_ref(command.get("quest_ref"), "quest_ref")
        correlation_ref = _require_ref(
            command.get("correlation_ref"), "correlation_ref"
        )
        operation = command.get("operation")
        if not isinstance(operation, str):
            raise BecomeQuestError("operation_required")
        snapshot = self.store.load(quest_ref)
        if operation == "CREATE_QUEST":
            if snapshot is not None:
                raise BecomeQuestError("quest_already_exists")
            mode = command.get("mode")
            if mode not in {"PRODUCTION", "SIMULATED"}:
                raise BecomeQuestError("mode_invalid")
            snapshot = QuestSnapshot(
                quest_ref=quest_ref,
                correlation_ref=correlation_ref,
                mode=str(mode),
            )
        elif snapshot is None:
            raise BecomeQuestError("quest_not_found")
        elif snapshot.correlation_ref != correlation_ref:
            raise BecomeQuestError("quest_correlation_mismatch")

        assert snapshot is not None
        if operation == "BIND_PARTICIPANT":
            self._bind(snapshot, command)
        elif operation == "EXPERT_CONSENT":
            self._expert_consent(snapshot, command)
        elif operation == "PROVIDER_TRANSCRIPT_SETTLED":
            self._provider_transcript(snapshot, command)
        elif operation == "REGISTER_ARTIFACT":
            self._register_artifact(snapshot, command)
        elif operation == "APPROVE_ARTIFACT":
            self._approve(snapshot, command)
        elif operation == "INVITE_LEARNER":
            self._invite(snapshot, command)
        elif operation == "ACCEPT_LEARNER":
            self._accept(snapshot, command)
        elif operation != "CREATE_QUEST":
            raise BecomeQuestError("operation_unsupported")
        self.store.save(snapshot)
        return {
            "schema": "gate.become-quest-operation@1",
            "quest_ref": snapshot.quest_ref,
            "correlation_ref": snapshot.correlation_ref,
            "operation": operation,
            "state": snapshot.state.value,
            "contract_version": QUEST_CONTRACT_VERSION,
            "mode": snapshot.mode,
            "external_effects": 0,
        }

    @staticmethod
    def _bind(snapshot: QuestSnapshot, command: JsonObject) -> None:
        try:
            role = QuestRole(str(command.get("role")))
        except ValueError as exc:
            raise BecomeQuestError("role_invalid") from exc
        person_ref = _require_ref(command.get("person_ref"), "person_ref")
        if snapshot.mode == "PRODUCTION" and person_ref.startswith("ref://simulated/"):
            raise BecomeQuestError("synthetic_participant_in_production")
        if role is QuestRole.EXPERT:
            if snapshot.learner_person_ref == person_ref:
                raise BecomeQuestError("roles_must_use_distinct_people")
            if snapshot.expert_person_ref not in {None, person_ref}:
                raise BecomeQuestError("role_rebinding_forbidden")
            if snapshot.expert_person_ref == person_ref:
                return
            snapshot.expert_person_ref = person_ref
            snapshot.state = QuestState.EXPERT_BOUND
        else:
            if snapshot.expert_person_ref == person_ref:
                raise BecomeQuestError("roles_must_use_distinct_people")
            if snapshot.learner_person_ref not in {None, person_ref}:
                raise BecomeQuestError("role_rebinding_forbidden")
            if snapshot.learner_person_ref == person_ref:
                return
            snapshot.learner_person_ref = person_ref

    @staticmethod
    def _expert_consent(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if snapshot.state is not QuestState.EXPERT_BOUND:
            raise BecomeQuestError("expert_consent_transition_blocked")
        if command.get("person_ref") != snapshot.expert_person_ref:
            raise BecomeQuestError("expert_only")
        _require_ref(command.get("consent_ref"), "consent_ref")
        snapshot.expert_consented = True
        snapshot.state = QuestState.EXPERT_CONSENTED

    @staticmethod
    def _provider_transcript(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if (
            not snapshot.expert_consented
            or snapshot.state is not QuestState.EXPERT_CONSENTED
        ):
            raise BecomeQuestError("expert_consent_required")
        digest = _require_digest(command.get("artifact_digest"), "artifact_digest")
        version = command.get("artifact_version")
        if not isinstance(version, int) or version < 1:
            raise BecomeQuestError("artifact_version_invalid")
        snapshot.artifacts[ArtifactType.TRANSCRIPT.value] = (version, digest)
        snapshot.approvals.pop(ArtifactType.TRANSCRIPT.value, None)
        snapshot.state = QuestState.TRANSCRIPT_PENDING_REVIEW

    @staticmethod
    def _register_artifact(snapshot: QuestSnapshot, command: JsonObject) -> None:
        try:
            artifact_type = ArtifactType(str(command.get("artifact_type")))
        except ValueError as exc:
            raise BecomeQuestError("artifact_type_invalid") from exc
        if artifact_type is ArtifactType.TRANSCRIPT:
            raise BecomeQuestError("provider_transcript_operation_required")
        if ArtifactType.TRANSCRIPT.value not in snapshot.approvals:
            raise BecomeQuestError("transcript_approval_required")
        digest = _require_digest(command.get("artifact_digest"), "artifact_digest")
        version = command.get("artifact_version")
        if not isinstance(version, int) or version < 1:
            raise BecomeQuestError("artifact_version_invalid")
        prior = snapshot.artifacts.get(artifact_type.value)
        if prior is not None and version <= prior[0]:
            raise BecomeQuestError("artifact_version_not_increased")
        snapshot.artifacts[artifact_type.value] = (version, digest)
        snapshot.approvals.pop(artifact_type.value, None)
        if artifact_type is ArtifactType.PRODUCT:
            snapshot.state = QuestState.PRODUCT_PENDING_APPROVAL

    @staticmethod
    def _approve(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if (
            command.get("approver_person_ref") != snapshot.expert_person_ref
            or command.get("approver_role") != QuestRole.EXPERT.value
        ):
            raise BecomeQuestError("approval_requires_quest_expert")
        try:
            artifact_type = ArtifactType(str(command.get("artifact_type")))
        except ValueError as exc:
            raise BecomeQuestError("artifact_type_invalid") from exc
        exact = snapshot.artifacts.get(artifact_type.value)
        if exact != (
            command.get("artifact_version"),
            command.get("artifact_digest"),
        ):
            raise BecomeQuestError("approval_artifact_version_mismatch")
        snapshot.approvals[artifact_type.value] = exact
        if artifact_type is ArtifactType.TRANSCRIPT:
            snapshot.state = QuestState.TRANSCRIPT_APPROVED
        if artifact_type is ArtifactType.PRODUCT:
            snapshot.state = QuestState.PRODUCT_APPROVED
        BecomeQuestCoordinator._advance(snapshot)

    @staticmethod
    def _invite(snapshot: QuestSnapshot, command: JsonObject) -> None:
        required = {
            ArtifactType.TRANSCRIPT.value,
            ArtifactType.EXPERT_PROFILE.value,
            ArtifactType.PRODUCT.value,
            ArtifactType.PROGRAM.value,
        }
        if not required.issubset(snapshot.approvals):
            raise BecomeQuestError("expert_artifact_approvals_required")
        if command.get("learner_person_ref") != snapshot.learner_person_ref:
            raise BecomeQuestError("learner_binding_required")
        _require_ref(command.get("invitation_ref"), "invitation_ref")
        snapshot.state = QuestState.LEARNER_INVITED

    @staticmethod
    def _accept(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if snapshot.state is not QuestState.LEARNER_INVITED:
            raise BecomeQuestError("learner_acceptance_transition_blocked")
        if command.get("learner_person_ref") != snapshot.learner_person_ref:
            raise BecomeQuestError("learner_only")
        _require_ref(command.get("acceptance_ref"), "acceptance_ref")
        snapshot.learner_enrolled = True
        snapshot.state = QuestState.LEARNER_ENROLLED
        BecomeQuestCoordinator._advance(snapshot)

    @staticmethod
    def _advance(snapshot: QuestSnapshot) -> None:
        required = {
            ArtifactType.TRANSCRIPT.value,
            ArtifactType.EXPERT_PROFILE.value,
            ArtifactType.PRODUCT.value,
            ArtifactType.PROGRAM.value,
        }
        if (
            snapshot.expert_consented
            and snapshot.learner_enrolled
            and required.issubset(snapshot.approvals)
        ):
            snapshot.state = QuestState.READY_FOR_EXERCISE
