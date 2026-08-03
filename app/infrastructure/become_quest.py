"""Durable, default-off persistence for role-correct BECOME Quests."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.canonical import Workspace
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_quest import (
    QUEST_CONTRACT_VERSION,
    QUEST_MAX_OPERATIONS,
    QUEST_PROJECTION_ENABLED,
)
from app.models.become_quest import (
    BecomeQuestApprovalModel,
    BecomeQuestEnrollmentModel,
    BecomeQuestModel,
    BecomeQuestOperationAttemptModel,
    BecomeQuestParticipantModel,
)


class SqlBecomeQuestError(RuntimeError):
    """Fail closed when a durable Quest projection conflicts with prior state."""


@dataclass(frozen=True, slots=True)
class QuestIdentity:
    quest_id: UUID
    quest_ref: str
    correlation_ref: str


@dataclass(frozen=True, slots=True)
class OperationReservation:
    attempt_id: UUID
    disposition: str


def _require_ref(value: str, name: str) -> str:
    if not value.startswith("ref://") or not 8 <= len(value) <= 160:
        raise SqlBecomeQuestError(f"{name}_invalid")
    return value


def _require_digest(value: str, name: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SqlBecomeQuestError(f"{name}_invalid")
    return value


class SqlBecomeQuestRepository:
    """Persist Quest identity, role bindings, and per-Quest attempt reservations."""

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
            workspace = await session.scalar(
                select(Workspace)
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
                .with_for_update()
            )
            if workspace is None:
                raise SqlBecomeQuestError("workspace_not_found")
            existing = await session.scalar(
                select(BecomeQuestModel)
                .where(BecomeQuestModel.quest_ref == quest_ref)
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.workspace_id != workspace.id
                    or existing.correlation_ref != correlation_ref
                    or existing.mode != mode
                    or existing.source_provenance_digest != source_provenance_digest
                ):
                    raise SqlBecomeQuestError("quest_identity_reused")
                return QuestIdentity(
                    quest_id=existing.id,
                    quest_ref=existing.quest_ref,
                    correlation_ref=existing.correlation_ref,
                )
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
            return QuestIdentity(
                quest_id=quest.id,
                quest_ref=quest.quest_ref,
                correlation_ref=quest.correlation_ref,
            )

    async def bind_participant(
        self,
        *,
        quest_ref: str,
        correlation_ref: str,
        role: str,
        person_ref: str,
        provenance_digest: str,
    ) -> str:
        _require_ref(person_ref, "person_ref")
        _require_digest(provenance_digest, "provenance_digest")
        if role not in {"EXPERT", "LEARNER"}:
            raise SqlBecomeQuestError("role_invalid")
        async with self.sessions() as session, session.begin():
            quest = await session.scalar(
                select(BecomeQuestModel)
                .where(BecomeQuestModel.quest_ref == quest_ref)
                .with_for_update()
            )
            if quest is None:
                raise SqlBecomeQuestError("quest_not_found")
            if quest.correlation_ref != correlation_ref:
                raise SqlBecomeQuestError("quest_correlation_mismatch")
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

    async def reserve_operation(
        self,
        *,
        quest_ref: str,
        correlation_ref: str,
        operation_type: str,
        attempt_version: int,
        request_digest: str,
    ) -> OperationReservation:
        _require_digest(request_digest, "request_digest")
        if not operation_type or attempt_version < 1:
            raise SqlBecomeQuestError("attempt_scope_invalid")
        async with self.sessions() as session, session.begin():
            quest = await session.scalar(
                select(BecomeQuestModel)
                .where(BecomeQuestModel.quest_ref == quest_ref)
                .with_for_update()
            )
            if quest is None:
                raise SqlBecomeQuestError("quest_not_found")
            if quest.correlation_ref != correlation_ref:
                raise SqlBecomeQuestError("quest_correlation_mismatch")
            existing = await session.scalar(
                select(BecomeQuestOperationAttemptModel)
                .where(
                    BecomeQuestOperationAttemptModel.quest_id == quest.id,
                    BecomeQuestOperationAttemptModel.correlation_ref == correlation_ref,
                    BecomeQuestOperationAttemptModel.operation_type == operation_type,
                    BecomeQuestOperationAttemptModel.attempt_version == attempt_version,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise SqlBecomeQuestError("mutated_operation_replay")
                return OperationReservation(
                    attempt_id=existing.id,
                    disposition="duplicate_suppressed",
                )
            attempt = BecomeQuestOperationAttemptModel(
                quest_id=quest.id,
                correlation_ref=correlation_ref,
                operation_type=operation_type,
                attempt_version=attempt_version,
                state="RESERVED",
                request_digest=request_digest,
                receipt_digest=None,
                receipt_payload=None,
                finished_at=None,
            )
            session.add(attempt)
            await session.flush()
            return OperationReservation(
                attempt_id=attempt.id,
                disposition="reserved",
            )


async def become_quest_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, object]:
    """Return a content-free read-only projection; all Quest effect gates stay off."""

    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.key == workspace_key)
    )
    if workspace_id is None:
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
    participant_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestParticipantModel)
            .where(BecomeQuestParticipantModel.quest_id.in_(quest_ids))
        )
        or 0
    )
    approval_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestApprovalModel)
            .where(BecomeQuestApprovalModel.quest_id.in_(quest_ids))
        )
        or 0
    )
    enrollment_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestEnrollmentModel)
            .where(BecomeQuestEnrollmentModel.quest_id.in_(quest_ids))
        )
        or 0
    )
    attempt_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestOperationAttemptModel)
            .where(BecomeQuestOperationAttemptModel.quest_id.in_(quest_ids))
        )
        or 0
    )
    unknown_count = int(
        await session.scalar(
            select(func.count())
            .select_from(BecomeQuestOperationAttemptModel)
            .where(
                BecomeQuestOperationAttemptModel.quest_id.in_(quest_ids),
                BecomeQuestOperationAttemptModel.state == "UNKNOWN",
            )
        )
        or 0
    )
    return {
        "quest_projection_enabled": QUEST_PROJECTION_ENABLED,
        "quest_activation_max_operations": QUEST_MAX_OPERATIONS,
        "quest_role_gate_enabled": False,
        "quest_approval_gate_enabled": False,
        "quest_count": quest_count,
        "quest_participant_count": participant_count,
        "quest_approval_count": approval_count,
        "quest_enrollment_count": enrollment_count,
        "quest_attempt_count": attempt_count,
        "quest_reconciliation_required": unknown_count > 0,
        "quest_external_effects": 0,
    }
