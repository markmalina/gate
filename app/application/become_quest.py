"""Canonical fail-closed role-correct BECOME Quest contract.

The synchronous coordinator and SnapshotQuestStore in this module are test fixtures.
Production orchestration is implemented by PostgresBecomeQuestCoordinator.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

QUEST_CONTRACT_VERSION = "become.role-correct-quest@1"
APPROVAL_SCHEMA = "become.quest-approval@1"
OPERATION_RECEIPT_SCHEMA = "become.quest-operation-receipt@1"
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


READINESS_APPROVALS = frozenset(
    {
        ArtifactType.TRANSCRIPT.value,
        ArtifactType.EXPERT_PROFILE.value,
        ArtifactType.PRODUCT.value,
        ArtifactType.PROGRAM.value,
    }
)
BASE_COMMAND_FIELDS = (
    "quest_id",
    "correlation_id",
    "operation",
    "attempt_version",
    "idempotency_key",
    "occurred_at",
)


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


def _require_version(value: object, field_name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise BecomeQuestError(f"{field_name}_invalid")
    return value


def _require_occurred_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise BecomeQuestError("occurred_at_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BecomeQuestError("occurred_at_invalid") from exc


def _require_command_envelope(command: JsonObject) -> None:
    for field_name in BASE_COMMAND_FIELDS:
        if command.get(field_name) in {None, ""}:
            raise BecomeQuestError(f"{field_name}_required")
    _require_ref(command.get("quest_id"), "quest_id")
    _require_ref(command.get("correlation_id"), "correlation_id")
    _require_version(command.get("attempt_version"), "attempt_version")
    _require_occurred_at(command.get("occurred_at"))


def operation_attempt_key(
    *,
    quest_id: str,
    correlation_id: str,
    operation: str,
    attempt_version: int,
    idempotency_key: str,
) -> str:
    _require_ref(quest_id, "quest_id")
    _require_ref(correlation_id, "correlation_id")
    _require_version(attempt_version, "attempt_version")
    if not operation or not idempotency_key:
        raise BecomeQuestError("attempt_scope_invalid")
    return "quest-attempt:" + canonical_digest(
        {
            "quest_id": quest_id,
            "correlation_id": correlation_id,
            "operation": operation,
            "attempt_version": attempt_version,
            "idempotency_key": idempotency_key,
        }
    )


def build_approval_receipt(
    *,
    quest_id: str,
    artifact_type: ArtifactType,
    artifact_version: int,
    artifact_digest: str,
    approver_person_ref: str,
    approved_at: datetime,
) -> JsonObject:
    _require_ref(quest_id, "quest_id")
    _require_ref(approver_person_ref, "approver_person_ref")
    _require_digest(artifact_digest, "artifact_digest")
    _require_version(artifact_version, "artifact_version")
    receipt: JsonObject = {
        "schema": APPROVAL_SCHEMA,
        "quest_id": quest_id,
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


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    artifact_version: int
    artifact_digest: str
    provenance_digest: str
    provider_settled: bool
    transcript_available: bool


@dataclass(frozen=True, slots=True)
class InvitationSnapshot:
    invitation_ref: str
    invitation_version: int
    invitation_digest: str
    learner_person_ref: str


@dataclass(slots=True)
class QuestSnapshot:
    quest_id: str
    correlation_id: str
    mode: str
    state: QuestState = QuestState.DRAFT
    expert_person_ref: str | None = None
    learner_person_ref: str | None = None
    expert_consented: bool = False
    learner_enrolled: bool = False
    artifacts: dict[str, dict[int, ArtifactSnapshot]] = field(default_factory=dict)
    approvals: dict[str, tuple[int, str]] = field(default_factory=dict)
    invitations: dict[int, InvitationSnapshot] = field(default_factory=dict)
    current_invitation_version: int | None = None


class SnapshotQuestStore:
    """Explicit test-only fixture; never use as a production Quest state source."""

    test_fixture_only = True

    def __init__(self, snapshot: dict[str, object] | None = None) -> None:
        raw = copy.deepcopy(snapshot or {"quests": {}, "replays": {}})
        self.quests: dict[str, QuestSnapshot] = raw["quests"]  # type: ignore[assignment]
        self.replays: dict[str, tuple[str, JsonObject]] = raw["replays"]  # type: ignore[assignment]

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy({"quests": self.quests, "replays": self.replays})

    def load(self, quest_id: str) -> QuestSnapshot | None:
        return copy.deepcopy(self.quests.get(quest_id))

    def save(self, snapshot: QuestSnapshot) -> None:
        self.quests[snapshot.quest_id] = copy.deepcopy(snapshot)

    def replay(self, scope_key: str) -> tuple[str, JsonObject] | None:
        return copy.deepcopy(self.replays.get(scope_key))

    def save_replay(
        self,
        scope_key: str,
        request_digest: str,
        receipt: JsonObject,
    ) -> None:
        self.replays[scope_key] = (request_digest, copy.deepcopy(receipt))


class BecomeQuestCoordinator:
    """Synchronous test coordinator mirroring PostgreSQL production semantics."""

    def __init__(self, store: SnapshotQuestStore) -> None:
        if not store.test_fixture_only:
            raise BecomeQuestError("snapshot_store_must_be_test_fixture")
        self.store = store

    def execute(self, command: JsonObject) -> JsonObject:
        _assert_content_free(command)
        _require_command_envelope(command)
        request_digest = canonical_digest(command)
        scope_key = operation_attempt_key(
            quest_id=str(command["quest_id"]),
            correlation_id=str(command["correlation_id"]),
            operation=str(command["operation"]),
            attempt_version=_require_version(
                command["attempt_version"], "attempt_version"
            ),
            idempotency_key=str(command["idempotency_key"]),
        )
        prior = self.store.replay(scope_key)
        if prior is not None:
            prior_digest, receipt = prior
            if prior_digest != request_digest:
                raise BecomeQuestError("mutated_replay")
            return receipt
        receipt = self._execute_new(command)
        self.store.save_replay(scope_key, request_digest, receipt)
        return receipt

    def _execute_new(self, command: JsonObject) -> JsonObject:
        quest_id = _require_ref(command.get("quest_id"), "quest_id")
        correlation_id = _require_ref(command.get("correlation_id"), "correlation_id")
        operation = str(command["operation"])
        snapshot = self.store.load(quest_id)
        if operation == "CREATE_QUEST":
            if snapshot is not None:
                raise BecomeQuestError("quest_already_exists")
            mode = command.get("mode")
            if mode not in {"PRODUCTION", "SIMULATED"}:
                raise BecomeQuestError("mode_invalid")
            _require_digest(
                command.get("source_provenance_digest"),
                "source_provenance_digest",
            )
            snapshot = QuestSnapshot(
                quest_id=quest_id,
                correlation_id=correlation_id,
                mode=str(mode),
            )
        elif snapshot is None:
            raise BecomeQuestError("quest_not_found")
        elif snapshot.correlation_id != correlation_id:
            raise BecomeQuestError("quest_correlation_mismatch")

        assert snapshot is not None
        handlers = {
            "BIND_PARTICIPANT": self._bind,
            "EXPERT_CONSENT": self._expert_consent,
            "PROVIDER_TRANSCRIPT_SETTLED": self._provider_transcript,
            "REGISTER_ARTIFACT": self._register_artifact,
            "APPROVE_ARTIFACT": self._approve,
            "INVITE_LEARNER": self._invite,
            "ACCEPT_LEARNER": self._accept,
        }
        if operation in handlers:
            handlers[operation](snapshot, command)
        elif operation != "CREATE_QUEST":
            raise BecomeQuestError("operation_unsupported")
        self.store.save(snapshot)
        return {
            "schema": OPERATION_RECEIPT_SCHEMA,
            "quest_id": snapshot.quest_id,
            "correlation_id": snapshot.correlation_id,
            "operation": operation,
            "attempt_version": command["attempt_version"],
            "idempotency_key": command["idempotency_key"],
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
        _require_digest(command.get("provenance_digest"), "provenance_digest")
        if snapshot.mode == "PRODUCTION" and person_ref.startswith("ref://simulated/"):
            raise BecomeQuestError("synthetic_participant_in_production")
        if role is QuestRole.EXPERT:
            if snapshot.learner_person_ref == person_ref:
                raise BecomeQuestError("roles_must_use_distinct_people")
            if snapshot.expert_person_ref not in {None, person_ref}:
                raise BecomeQuestError("role_rebinding_forbidden")
            snapshot.expert_person_ref = person_ref
            snapshot.state = QuestState.EXPERT_BOUND
        else:
            if snapshot.expert_person_ref == person_ref:
                raise BecomeQuestError("roles_must_use_distinct_people")
            if snapshot.learner_person_ref not in {None, person_ref}:
                raise BecomeQuestError("role_rebinding_forbidden")
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
    def _store_artifact(
        snapshot: QuestSnapshot,
        command: JsonObject,
        artifact_type: ArtifactType,
        *,
        provider_settled: bool,
        transcript_available: bool,
    ) -> None:
        artifact_digest = _require_digest(
            command.get("artifact_digest"), "artifact_digest"
        )
        provenance_digest = _require_digest(
            command.get("provenance_digest"), "provenance_digest"
        )
        version = _require_version(command.get("artifact_version"), "artifact_version")
        versions = snapshot.artifacts.setdefault(artifact_type.value, {})
        latest_version = max(versions, default=0)
        if version < latest_version:
            raise BecomeQuestError("artifact_version_not_increased")
        if version == latest_version:
            if versions[version].artifact_digest != artifact_digest:
                raise BecomeQuestError("artifact_version_mutation")
            raise BecomeQuestError("artifact_version_not_increased")
        versions[version] = ArtifactSnapshot(
            artifact_version=version,
            artifact_digest=artifact_digest,
            provenance_digest=provenance_digest,
            provider_settled=provider_settled,
            transcript_available=transcript_available,
        )
        snapshot.approvals.pop(artifact_type.value, None)

    @staticmethod
    def _provider_transcript(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if not snapshot.expert_consented:
            raise BecomeQuestError("expert_consent_required")
        BecomeQuestCoordinator._store_artifact(
            snapshot,
            command,
            ArtifactType.TRANSCRIPT,
            provider_settled=True,
            transcript_available=True,
        )
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
        BecomeQuestCoordinator._store_artifact(
            snapshot,
            command,
            artifact_type,
            provider_settled=False,
            transcript_available=False,
        )
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
        versions = snapshot.artifacts.get(artifact_type.value, {})
        if not versions:
            raise BecomeQuestError("artifact_not_found")
        latest = versions[max(versions)]
        if (
            latest.artifact_version != command.get("artifact_version")
            or latest.artifact_digest != command.get("artifact_digest")
        ):
            raise BecomeQuestError("approval_artifact_version_mismatch")
        snapshot.approvals[artifact_type.value] = (
            latest.artifact_version,
            latest.artifact_digest,
        )
        if artifact_type is ArtifactType.TRANSCRIPT:
            snapshot.state = QuestState.TRANSCRIPT_APPROVED
        if artifact_type is ArtifactType.PRODUCT:
            snapshot.state = QuestState.PRODUCT_APPROVED
        BecomeQuestCoordinator._advance(snapshot)

    @staticmethod
    def _invite(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if not READINESS_APPROVALS.issubset(snapshot.approvals):
            raise BecomeQuestError("expert_artifact_approvals_required")
        learner = _require_ref(command.get("learner_person_ref"), "learner_person_ref")
        if learner != snapshot.learner_person_ref:
            raise BecomeQuestError("learner_binding_required")
        invitation_ref = _require_ref(command.get("invitation_ref"), "invitation_ref")
        invitation_digest = _require_digest(
            command.get("invitation_digest"), "invitation_digest"
        )
        invitation_version = _require_version(
            command.get("invitation_version"), "invitation_version"
        )
        current = (
            snapshot.invitations.get(snapshot.current_invitation_version)
            if snapshot.current_invitation_version is not None
            else None
        )
        if current is not None and invitation_version <= current.invitation_version:
            if invitation_version == current.invitation_version and (
                invitation_ref != current.invitation_ref
                or invitation_digest != current.invitation_digest
            ):
                raise BecomeQuestError("invitation_version_mutation")
            raise BecomeQuestError("invitation_version_not_increased")
        snapshot.invitations[invitation_version] = InvitationSnapshot(
            invitation_ref=invitation_ref,
            invitation_version=invitation_version,
            invitation_digest=invitation_digest,
            learner_person_ref=learner,
        )
        snapshot.current_invitation_version = invitation_version
        snapshot.state = QuestState.LEARNER_INVITED

    @staticmethod
    def _accept(snapshot: QuestSnapshot, command: JsonObject) -> None:
        if (
            snapshot.state is not QuestState.LEARNER_INVITED
            or snapshot.current_invitation_version is None
        ):
            raise BecomeQuestError("learner_acceptance_transition_blocked")
        if command.get("learner_person_ref") != snapshot.learner_person_ref:
            raise BecomeQuestError("learner_only")
        invitation = snapshot.invitations[snapshot.current_invitation_version]
        if (
            command.get("invitation_ref") != invitation.invitation_ref
            or command.get("invitation_version") != invitation.invitation_version
            or command.get("invitation_digest") != invitation.invitation_digest
        ):
            raise BecomeQuestError("learner_invitation_mismatch")
        _require_ref(command.get("acceptance_ref"), "acceptance_ref")
        _require_digest(command.get("acceptance_digest"), "acceptance_digest")
        snapshot.learner_enrolled = True
        snapshot.state = QuestState.LEARNER_ENROLLED
        BecomeQuestCoordinator._advance(snapshot)

    @staticmethod
    def _advance(snapshot: QuestSnapshot) -> None:
        if (
            snapshot.expert_consented
            and snapshot.learner_enrolled
            and READINESS_APPROVALS.issubset(snapshot.approvals)
        ):
            snapshot.state = QuestState.READY_FOR_EXERCISE
