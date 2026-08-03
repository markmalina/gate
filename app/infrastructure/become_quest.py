"""Transactional PostgreSQL source of truth for role-correct BECOME Quests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_quest import (
    OPERATION_RECEIPT_SCHEMA,
    QUEST_CONTRACT_VERSION,
    QUEST_MAX_OPERATIONS,
    QUEST_PROJECTION_ENABLED,
    READINESS_APPROVALS,
    ArtifactType,
    JsonObject,
    QuestRole,
    canonical_digest,
)
from app.models.become_quest import (
    BecomeQuestApprovalModel,
    BecomeQuestArtifactModel,
    BecomeQuestEnrollmentModel,
    BecomeQuestModel,
    BecomeQuestOperationAttemptModel,
    BecomeQuestParticipantModel,
)
from app.models.canonical import Workspace

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


class SqlBecomeQuestError(RuntimeError):
    """Fail closed when durable Quest state conflicts with the canonical contract."""


@dataclass(frozen=True, slots=True)
class QuestIdentity:
    quest_id: UUID
    quest_ref: str
    correlation_ref: str


@dataclass(frozen=True, slots=True)
class OperationReservation:
    attempt_id: UUID
    disposition: str


def _require_ref(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("ref://"):
        raise SqlBecomeQuestError(f"{name}_invalid")
    if not 8 <= len(value) <= 160:
        raise SqlBecomeQuestError(f"{name}_invalid")
    return value


def _require_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise SqlBecomeQuestError(f"{name}_invalid")
    return value


def _require_version(value: object, name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise SqlBecomeQuestError(f"{name}_invalid")
    return value


def _require_occurred_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise SqlBecomeQuestError("occurred_at_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SqlBecomeQuestError("occurred_at_invalid") from exc


def _assert_content_free(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SENSITIVE_FIELDS:
                raise SqlBecomeQuestError("sensitive_field_forbidden")
            _assert_content_free(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_content_free(nested)


def _require_command(command: JsonObject) -> tuple[str, str, str, int, str, datetime]:
    for field_name in (
        "quest_id",
        "correlation_id",
        "operation",
        "attempt_version",
        "idempotency_key",
        "occurred_at",
    ):
        if command.get(field_name) in {None, ""}:
            raise SqlBecomeQuestError(f"{field_name}_required")
    quest_id = _require_ref(command.get("quest_id"), "quest_id")
    correlation_id = _require_ref(command.get("correlation_id"), "correlation_id")
    operation = str(command["operation"])
    attempt_version = _require_version(
        command.get("attempt_version"), "attempt_version"
    )
    idempotency_key = str(command["idempotency_key"])
    occurred_at = _require_occurred_at(command.get("occurred_at"))
    return (
        quest_id,
        correlation_id,
        operation,
        attempt_version,
        idempotency_key,
        occurred_at,
    )


class SqlBecomeQuestRepository:
    """Low-level durable reservations used by the production coordinator."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_quest(
        self,
        *,
        workspace_key: str,
        quest_ref: str,
        correlation_ref: str,
        mode: str,
        source_provenance_digest: str,
    ) -> QuestIdentity:
        _require_ref(quest_ref, "quest_ref")
        _require_ref(correlation_ref, "correlation_ref")
        _require_digest(source_provenance_digest, "source_provenance_digest")
        if mode not in {"PRODUCTION", "SIMULATED"}:
            raise SqlBecomeQuestError("mode_invalid")
        async with self.sessions() as session, session.begin():
            workspace = await _lock_workspace(session, workspace_key)
            existing = await _lock_quest(session, quest_ref)
            if existing is not None:
                if (
                    existing.workspace_id != workspace.id
                    or existing.correlation_ref != correlation_ref
                    or existing.mode != mode
                    or existing.source_provenance_digest != source_provenance_digest
                ):
                    raise SqlBecomeQuestError("quest_identity_reused")
                return _identity(existing)
            quest = BecomeQuestModel(
                workspace_id=workspace.id,
                quest_ref=quest_ref,
                correlation_ref=correlation_ref,
                contract_version=QUEST_CONTRACT_VERSION,
                state="DRAFT",
                expert_person_ref=None,
                learner_person_ref=None,
                mode=mode,
                source_provenance_digest=source_provenance_digest,
            )
            session.add(quest)
            await session.flush()
            return _identity(quest)

    async def bind_participant(
        self,
        *,
        quest_ref: str,
        correlation_ref: str,
        role: str,
        person_ref: str,
        provenance_digest: str,
    ) -> str:
        async with self.sessions() as session, session.begin():
            quest = await _require_locked_quest(session, quest_ref, correlation_ref)
            return await _bind_participant(
                session,
                quest,
                role=role,
                person_ref=person_ref,
                provenance_digest=provenance_digest,
            )

    async def reserve_operation(
        self,
        *,
        quest_ref: str,
        correlation_ref: str,
        operation_type: str,
        attempt_version: int,
        idempotency_key: str,
        request_digest: str,
    ) -> OperationReservation:
        _require_digest(request_digest, "request_digest")
        _require_version(attempt_version, "attempt_version")
        if not operation_type or not idempotency_key:
            raise SqlBecomeQuestError("attempt_scope_invalid")
        async with self.sessions() as session, session.begin():
            quest = await _require_locked_quest(session, quest_ref, correlation_ref)
            existing = await _lock_attempt(
                session,
                quest,
                operation_type,
                attempt_version,
                idempotency_key,
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise SqlBecomeQuestError("mutated_operation_replay")
                return OperationReservation(existing.id, existing.state.lower())
            attempt = BecomeQuestOperationAttemptModel(
                quest_id=quest.id,
                correlation_ref=correlation_ref,
                operation_type=operation_type,
                attempt_version=attempt_version,
                idempotency_key=idempotency_key,
                state="RESERVED",
                request_digest=request_digest,
                receipt_digest=None,
                receipt_payload=None,
                finished_at=None,
            )
            session.add(attempt)
            await session.flush()
            return OperationReservation(attempt.id, "reserved")


class PostgresBecomeQuestCoordinator:
    """Production Quest coordinator; every transition and replay is transactional."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        workspace_key: str,
    ) -> None:
        self.sessions = sessions
        self.workspace_key = workspace_key

    async def execute(self, command: JsonObject) -> JsonObject:
        _assert_content_free(command)
        (
            quest_ref,
            correlation_ref,
            operation,
            attempt_version,
            idempotency_key,
            occurred_at,
        ) = _require_command(command)
        request_digest = canonical_digest(command)
        async with self.sessions() as session, session.begin():
            quest = await _lock_quest(session, quest_ref)
            if operation == "CREATE_QUEST":
                quest = await self._create_or_validate(
                    session,
                    quest,
                    command,
                    quest_ref,
                    correlation_ref,
                )
            elif quest is None:
                raise SqlBecomeQuestError("quest_not_found")
            elif quest.correlation_ref != correlation_ref:
                raise SqlBecomeQuestError("quest_correlation_mismatch")
            assert quest is not None

            attempt = await _lock_attempt(
                session,
                quest,
                operation,
                attempt_version,
                idempotency_key,
            )
            if attempt is not None:
                if attempt.request_digest != request_digest:
                    raise SqlBecomeQuestError("mutated_replay")
                if attempt.state == "COMPLETED" and attempt.receipt_payload is not None:
                    return dict(attempt.receipt_payload)
                if attempt.state == "RESERVED":
                    raise SqlBecomeQuestError("operation_reconciliation_required")
                raise SqlBecomeQuestError("terminal_operation_not_retriable")

            attempt = BecomeQuestOperationAttemptModel(
                quest_id=quest.id,
                correlation_ref=correlation_ref,
                operation_type=operation,
                attempt_version=attempt_version,
                idempotency_key=idempotency_key,
                state="RESERVED",
                request_digest=request_digest,
                receipt_digest=None,
                receipt_payload=None,
                finished_at=None,
            )
            session.add(attempt)
            await session.flush()
            await self._apply(session, quest, operation, command, occurred_at)
            receipt: JsonObject = {
                "schema": OPERATION_RECEIPT_SCHEMA,
                "quest_id": quest.quest_ref,
                "correlation_id": quest.correlation_ref,
                "operation": operation,
                "attempt_version": attempt_version,
                "idempotency_key": idempotency_key,
                "state": quest.state,
                "contract_version": QUEST_CONTRACT_VERSION,
                "mode": quest.mode,
                "external_effects": 0,
            }
            attempt.state = "COMPLETED"
            attempt.receipt_payload = receipt
            attempt.receipt_digest = canonical_digest(receipt)
            attempt.finished_at = occurred_at
            quest.updated_at = occurred_at
            await session.flush()
            return dict(receipt)

    async def _create_or_validate(
        self,
        session: AsyncSession,
        quest: BecomeQuestModel | None,
        command: JsonObject,
        quest_ref: str,
        correlation_ref: str,
    ) -> BecomeQuestModel:
        mode = command.get("mode")
        if mode not in {"PRODUCTION", "SIMULATED"}:
            raise SqlBecomeQuestError("mode_invalid")
        source_digest = _require_digest(
            command.get("source_provenance_digest"),
            "source_provenance_digest",
        )
        workspace = await _lock_workspace(session, self.workspace_key)
        if quest is None:
            quest = await _lock_quest(session, quest_ref)
        if quest is not None:
            if (
                quest.workspace_id != workspace.id
                or quest.correlation_ref != correlation_ref
                or quest.mode != mode
                or quest.source_provenance_digest != source_digest
            ):
                raise SqlBecomeQuestError("quest_identity_reused")
            return quest
        quest = BecomeQuestModel(
            workspace_id=workspace.id,
            quest_ref=quest_ref,
            correlation_ref=correlation_ref,
            contract_version=QUEST_CONTRACT_VERSION,
            state="DRAFT",
            expert_person_ref=None,
            learner_person_ref=None,
            mode=str(mode),
            source_provenance_digest=source_digest,
        )
        session.add(quest)
        await session.flush()
        return quest

    async def _apply(
        self,
        session: AsyncSession,
        quest: BecomeQuestModel,
        operation: str,
        command: JsonObject,
        occurred_at: datetime,
    ) -> None:
        if operation == "CREATE_QUEST":
            return
        if operation == "BIND_PARTICIPANT":
            await _bind_participant(
                session,
                quest,
                role=str(command.get("role")),
                person_ref=str(command.get("person_ref")),
                provenance_digest=str(command.get("provenance_digest")),
            )
        elif operation == "EXPERT_CONSENT":
            await self._expert_consent(quest, command)
        elif operation == "PROVIDER_TRANSCRIPT_SETTLED":
            await self._provider_transcript(session, quest, command)
        elif operation == "REGISTER_ARTIFACT":
            await self._register_artifact(session, quest, command)
        elif operation == "APPROVE_ARTIFACT":
            await self._approve(session, quest, command, occurred_at)
        elif operation == "INVITE_LEARNER":
            await self._invite(session, quest, command, occurred_at)
        elif operation == "ACCEPT_LEARNER":
            await self._accept(session, quest, command, occurred_at)
        else:
            raise SqlBecomeQuestError("operation_unsupported")

    @staticmethod
    async def _expert_consent(
        quest: BecomeQuestModel,
        command: JsonObject,
    ) -> None:
        if quest.state != "EXPERT_BOUND":
            raise SqlBecomeQuestError("expert_consent_transition_blocked")
        if command.get("person_ref") != quest.expert_person_ref:
            raise SqlBecomeQuestError("expert_only")
        _require_ref(command.get("consent_ref"), "consent_ref")
        quest.state = "EXPERT_CONSENTED"

    @staticmethod
    async def _provider_transcript(
        session: AsyncSession,
        quest: BecomeQuestModel,
        command: JsonObject,
    ) -> None:
        if quest.state not in {
            "EXPERT_CONSENTED",
            "TRANSCRIPT_PENDING_REVIEW",
            "TRANSCRIPT_APPROVED",
            "PRODUCT_PENDING_APPROVAL",
            "PRODUCT_APPROVED",
            "LEARNER_INVITED",
            "LEARNER_ENROLLED",
            "READY_FOR_EXERCISE",
        }:
            raise SqlBecomeQuestError("expert_consent_required")
        await _append_artifact(
            session,
            quest,
            command,
            ArtifactType.TRANSCRIPT,
            provider_settled=True,
            transcript_available=True,
        )
        quest.state = "TRANSCRIPT_PENDING_REVIEW"

    @staticmethod
    async def _register_artifact(
        session: AsyncSession,
        quest: BecomeQuestModel,
        command: JsonObject,
    ) -> None:
        try:
            artifact_type = ArtifactType(str(command.get("artifact_type")))
        except ValueError as exc:
            raise SqlBecomeQuestError("artifact_type_invalid") from exc
        if artifact_type is ArtifactType.TRANSCRIPT:
            raise SqlBecomeQuestError("provider_transcript_operation_required")
        if not await _latest_artifact_is_approved(
            session, quest.id, ArtifactType.TRANSCRIPT.value
        ):
            raise SqlBecomeQuestError("transcript_approval_required")
        await _append_artifact(
            session,
            quest,
            command,
            artifact_type,
            provider_settled=False,
            transcript_available=False,
        )
        if artifact_type is ArtifactType.PRODUCT:
            quest.state = "PRODUCT_PENDING_APPROVAL"

    @staticmethod
    async def _approve(
        session: AsyncSession,
        quest: BecomeQuestModel,
        command: JsonObject,
        occurred_at: datetime,
    ) -> None:
        if (
            command.get("approver_person_ref") != quest.expert_person_ref
            or command.get("approver_role") != QuestRole.EXPERT.value
        ):
            raise SqlBecomeQuestError("approval_requires_quest_expert")
        try:
            artifact_type = ArtifactType(str(command.get("artifact_type")))
        except ValueError as exc:
            raise SqlBecomeQuestError("artifact_type_invalid") from exc
        artifact = await _latest_artifact(session, quest.id, artifact_type.value)
        if artifact is None:
            raise SqlBecomeQuestError("artifact_not_found")
        if (
            artifact.artifact_version != command.get("artifact_version")
            or artifact.artifact_digest != command.get("artifact_digest")
        ):
            raise SqlBecomeQuestError("approval_artifact_version_mismatch")
        existing = await session.scalar(
            select(BecomeQuestApprovalModel).where(
                BecomeQuestApprovalModel.artifact_id == artifact.id
            )
        )
        if existing is None:
            receipt: JsonObject = {
                "schema": "become.quest-approval@1",
                "quest_id": quest.quest_ref,
                "artifact_type": artifact_type.value,
                "artifact_version": artifact.artifact_version,
                "artifact_digest": artifact.artifact_digest,
                "approver_person_ref": str(command["approver_person_ref"]),
                "approver_role": QuestRole.EXPERT.value,
                "approval_state": "APPROVED",
                "approved_at": occurred_at.isoformat(),
                "contract_version": QUEST_CONTRACT_VERSION,
                "external_effects": 0,
            }
            session.add(
                BecomeQuestApprovalModel(
                    quest_id=quest.id,
                    artifact_id=artifact.id,
                    approver_person_ref=str(command["approver_person_ref"]),
                    approver_role=QuestRole.EXPERT.value,
                    approval_state="APPROVED",
                    approved_at=occurred_at,
                    contract_version=QUEST_CONTRACT_VERSION,
                    receipt_digest=canonical_digest(receipt),
                    receipt_payload=receipt,
                )
            )
        if artifact_type is ArtifactType.TRANSCRIPT:
            quest.state = "TRANSCRIPT_APPROVED"
        elif artifact_type is ArtifactType.PRODUCT:
            quest.state = "PRODUCT_APPROVED"
        await _advance_ready(session, quest)

    @staticmethod
    async def _invite(
        session: AsyncSession,
        quest: BecomeQuestModel,
        command: JsonObject,
        occurred_at: datetime,
    ) -> None:
        if not await _has_readiness_approvals(session, quest.id):
            raise SqlBecomeQuestError("expert_artifact_approvals_required")
        learner = _require_ref(command.get("learner_person_ref"), "learner_person_ref")
        if learner != quest.learner_person_ref:
            raise SqlBecomeQuestError("learner_binding_required")
        invitation_ref = _require_ref(command.get("invitation_ref"), "invitation_ref")
        invitation_version = _require_version(
            command.get("invitation_version"), "invitation_version"
        )
        invitation_digest = _require_digest(
            command.get("invitation_digest"), "invitation_digest"
        )
        current = await _latest_invitation(session, quest.id)
        if current is not None and invitation_version <= current.invitation_version:
            if invitation_version == current.invitation_version and (
                invitation_ref != current.invitation_ref
                or invitation_digest != current.invitation_digest
            ):
                raise SqlBecomeQuestError("invitation_version_mutation")
            raise SqlBecomeQuestError("invitation_version_not_increased")
        session.add(
            BecomeQuestEnrollmentModel(
                quest_id=quest.id,
                learner_person_ref=learner,
                learner_role="LEARNER",
                learner_active=True,
                state="INVITED",
                invitation_ref=invitation_ref,
                invitation_version=invitation_version,
                invitation_digest=invitation_digest,
                acceptance_digest=None,
                invited_at=occurred_at,
                accepted_at=None,
            )
        )
        quest.state = "LEARNER_INVITED"

    @staticmethod
    async def _accept(
        session: AsyncSession,
        quest: BecomeQuestModel,
        command: JsonObject,
        occurred_at: datetime,
    ) -> None:
        if quest.state != "LEARNER_INVITED":
            raise SqlBecomeQuestError("learner_acceptance_transition_blocked")
        if command.get("learner_person_ref") != quest.learner_person_ref:
            raise SqlBecomeQuestError("learner_only")
        invitation = await _latest_invitation(session, quest.id)
        if invitation is None:
            raise SqlBecomeQuestError("learner_acceptance_transition_blocked")
        if (
            command.get("invitation_ref") != invitation.invitation_ref
            or command.get("invitation_version") != invitation.invitation_version
            or command.get("invitation_digest") != invitation.invitation_digest
        ):
            raise SqlBecomeQuestError("learner_invitation_mismatch")
        _require_ref(command.get("acceptance_ref"), "acceptance_ref")
        invitation.acceptance_digest = _require_digest(
            command.get("acceptance_digest"), "acceptance_digest"
        )
        invitation.accepted_at = occurred_at
        invitation.state = "ENROLLED"
        quest.state = "LEARNER_ENROLLED"
        await _advance_ready(session, quest)


async def _lock_workspace(session: AsyncSession, key: str) -> Workspace:
    workspace = await session.scalar(
        select(Workspace)
        .where(Workspace.key == key, Workspace.is_active.is_(True))
        .with_for_update()
    )
    if workspace is None:
        raise SqlBecomeQuestError("workspace_not_found")
    return workspace


async def _lock_quest(
    session: AsyncSession,
    quest_ref: str,
) -> BecomeQuestModel | None:
    return cast(
        BecomeQuestModel | None,
        await session.scalar(
            select(BecomeQuestModel)
            .where(BecomeQuestModel.quest_ref == quest_ref)
            .with_for_update()
        ),
    )


async def _require_locked_quest(
    session: AsyncSession,
    quest_ref: str,
    correlation_ref: str,
) -> BecomeQuestModel:
    quest = await _lock_quest(session, quest_ref)
    if quest is None:
        raise SqlBecomeQuestError("quest_not_found")
    if quest.correlation_ref != correlation_ref:
        raise SqlBecomeQuestError("quest_correlation_mismatch")
    return quest


def _identity(quest: BecomeQuestModel) -> QuestIdentity:
    return QuestIdentity(quest.id, quest.quest_ref, quest.correlation_ref)


async def _bind_participant(
    session: AsyncSession,
    quest: BecomeQuestModel,
    *,
    role: str,
    person_ref: str,
    provenance_digest: str,
) -> str:
    _require_ref(person_ref, "person_ref")
    _require_digest(provenance_digest, "provenance_digest")
    if role not in {"EXPERT", "LEARNER"}:
        raise SqlBecomeQuestError("role_invalid")
    if quest.mode == "PRODUCTION" and person_ref.startswith("ref://simulated/"):
        raise SqlBecomeQuestError("synthetic_participant_in_production")
    bindings = (
        await session.scalars(
            select(BecomeQuestParticipantModel)
            .where(
                BecomeQuestParticipantModel.quest_id == quest.id,
                BecomeQuestParticipantModel.active.is_(True),
            )
            .with_for_update()
        )
    ).all()
    same_role = next((item for item in bindings if item.role == role), None)
    if same_role is not None:
        if (
            same_role.person_ref != person_ref
            or same_role.provenance_digest != provenance_digest
        ):
            raise SqlBecomeQuestError("role_rebinding_forbidden")
        return "already_current"
    if any(item.person_ref == person_ref for item in bindings):
        raise SqlBecomeQuestError("roles_must_use_distinct_people")
    session.add(
        BecomeQuestParticipantModel(
            quest_id=quest.id,
            role=role,
            person_ref=person_ref,
            provenance_digest=provenance_digest,
            mode=quest.mode,
            active=True,
        )
    )
    if role == "EXPERT":
        quest.expert_person_ref = person_ref
        quest.state = "EXPERT_BOUND"
    else:
        quest.learner_person_ref = person_ref
    await session.flush()
    return "created"


async def _lock_attempt(
    session: AsyncSession,
    quest: BecomeQuestModel,
    operation: str,
    attempt_version: int,
    idempotency_key: str,
) -> BecomeQuestOperationAttemptModel | None:
    return cast(
        BecomeQuestOperationAttemptModel | None,
        await session.scalar(
            select(BecomeQuestOperationAttemptModel)
            .where(
                BecomeQuestOperationAttemptModel.quest_id == quest.id,
                BecomeQuestOperationAttemptModel.correlation_ref
                == quest.correlation_ref,
                BecomeQuestOperationAttemptModel.operation_type == operation,
                BecomeQuestOperationAttemptModel.attempt_version == attempt_version,
                BecomeQuestOperationAttemptModel.idempotency_key == idempotency_key,
            )
            .with_for_update()
        ),
    )


async def _latest_artifact(
    session: AsyncSession,
    quest_id: UUID,
    artifact_type: str,
) -> BecomeQuestArtifactModel | None:
    return cast(
        BecomeQuestArtifactModel | None,
        await session.scalar(
            select(BecomeQuestArtifactModel)
            .where(
                BecomeQuestArtifactModel.quest_id == quest_id,
                BecomeQuestArtifactModel.artifact_type == artifact_type,
            )
            .order_by(BecomeQuestArtifactModel.artifact_version.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _append_artifact(
    session: AsyncSession,
    quest: BecomeQuestModel,
    command: JsonObject,
    artifact_type: ArtifactType,
    *,
    provider_settled: bool,
    transcript_available: bool,
) -> BecomeQuestArtifactModel:
    version = _require_version(command.get("artifact_version"), "artifact_version")
    artifact_digest = _require_digest(
        command.get("artifact_digest"), "artifact_digest"
    )
    provenance_digest = _require_digest(
        command.get("provenance_digest"), "provenance_digest"
    )
    latest = await _latest_artifact(session, quest.id, artifact_type.value)
    if latest is not None and version < latest.artifact_version:
        raise SqlBecomeQuestError("artifact_version_not_increased")
    if latest is not None and version == latest.artifact_version:
        if latest.artifact_digest != artifact_digest:
            raise SqlBecomeQuestError("artifact_version_mutation")
        raise SqlBecomeQuestError("artifact_version_not_increased")
    artifact = BecomeQuestArtifactModel(
        quest_id=quest.id,
        artifact_type=artifact_type.value,
        artifact_version=version,
        artifact_digest=artifact_digest,
        provider_settled=provider_settled,
        transcript_available=transcript_available,
        expert_reviewed=False,
        expert_approved=False,
        provenance_digest=provenance_digest,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def _latest_artifact_is_approved(
    session: AsyncSession,
    quest_id: UUID,
    artifact_type: str,
) -> bool:
    artifact = await _latest_artifact(session, quest_id, artifact_type)
    if artifact is None:
        return False
    return (
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestApprovalModel)
            .where(
                BecomeQuestApprovalModel.quest_id == quest_id,
                BecomeQuestApprovalModel.artifact_id == artifact.id,
            )
        )
        or 0
    ) == 1


async def _has_readiness_approvals(session: AsyncSession, quest_id: UUID) -> bool:
    for artifact_type in READINESS_APPROVALS:
        if not await _latest_artifact_is_approved(session, quest_id, artifact_type):
            return False
    return True


async def _latest_invitation(
    session: AsyncSession,
    quest_id: UUID,
) -> BecomeQuestEnrollmentModel | None:
    return cast(
        BecomeQuestEnrollmentModel | None,
        await session.scalar(
            select(BecomeQuestEnrollmentModel)
            .where(BecomeQuestEnrollmentModel.quest_id == quest_id)
            .order_by(BecomeQuestEnrollmentModel.invitation_version.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _advance_ready(
    session: AsyncSession,
    quest: BecomeQuestModel,
) -> None:
    invitation = await _latest_invitation(session, quest.id)
    if (
        invitation is not None
        and invitation.state == "ENROLLED"
        and await _has_readiness_approvals(session, quest.id)
    ):
        quest.state = "READY_FOR_EXERCISE"


async def become_quest_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, object]:
    """Return content-free read-only projection; every Quest effect gate stays off."""

    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.key == workspace_key)
    )
    if workspace_id is None:
        return _empty_status()
    quest_ids = select(BecomeQuestModel.id).where(
        BecomeQuestModel.workspace_id == workspace_id
    )

    quest_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestModel)
            .where(BecomeQuestModel.workspace_id == workspace_id)
        )
        or 0
    )
    unknown_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestOperationAttemptModel)
            .where(
                BecomeQuestOperationAttemptModel.quest_id.in_(quest_ids),
                BecomeQuestOperationAttemptModel.state.in_({"RESERVED", "UNKNOWN"}),
            )
        )
        or 0
    )
    return {
        **_empty_status(),
        "quest_count": quest_count,
        "quest_participant_count": int(
            await session.scalar(
                select(func.count())
                .select_from(BecomeQuestParticipantModel)
                .where(BecomeQuestParticipantModel.quest_id.in_(quest_ids))
            )
            or 0
        ),
        "quest_approval_count": int(
            await session.scalar(
                select(func.count())
                .select_from(BecomeQuestApprovalModel)
                .where(BecomeQuestApprovalModel.quest_id.in_(quest_ids))
            )
            or 0
        ),
        "quest_enrollment_count": int(
            await session.scalar(
                select(func.count())
                .select_from(BecomeQuestEnrollmentModel)
                .where(BecomeQuestEnrollmentModel.quest_id.in_(quest_ids))
            )
            or 0
        ),
        "quest_attempt_count": int(
            await session.scalar(
                select(func.count())
                .select_from(BecomeQuestOperationAttemptModel)
                .where(BecomeQuestOperationAttemptModel.quest_id.in_(quest_ids))
            )
            or 0
        ),
        "quest_reconciliation_required": unknown_count > 0,
    }


def _empty_status() -> dict[str, object]:
    return {
        "quest_projection_enabled": QUEST_PROJECTION_ENABLED,
        "quest_activation_max_operations": QUEST_MAX_OPERATIONS,
        "quest_role_gate_enabled": False,
        "quest_approval_gate_enabled": False,
        "quest_count": 0,
        "quest_participant_count": 0,
        "quest_approval_count": 0,
        "quest_enrollment_count": 0,
        "quest_attempt_count": 0,
        "quest_reconciliation_required": False,
        "quest_external_effects": 0,
    }
