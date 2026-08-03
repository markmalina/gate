"""Durable, content-free state for role-correct BECOME Quests."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BecomeQuestModel(Base):
    __tablename__ = "become_quests"
    __table_args__ = (
        UniqueConstraint("quest_ref", name="uq_become_quest_ref"),
        UniqueConstraint(
            "id", "correlation_ref", name="uq_become_quest_id_correlation"
        ),
        UniqueConstraint("id", "mode", name="uq_become_quest_id_mode"),
        CheckConstraint(
            "contract_version = 'become.role-correct-quest@1'",
            name="ck_become_quest_contract",
        ),
        CheckConstraint(
            "state IN ('DRAFT','EXPERT_BOUND','EXPERT_CONSENTED',"
            "'TRANSCRIPT_PENDING_REVIEW','TRANSCRIPT_APPROVED',"
            "'PRODUCT_PENDING_APPROVAL','PRODUCT_APPROVED',"
            "'LEARNER_INVITED','LEARNER_ENROLLED','READY_FOR_EXERCISE',"
            "'BLOCKED','TERMINAL')",
            name="ck_become_quest_state",
        ),
        CheckConstraint(
            "mode IN ('PRODUCTION','SIMULATED')",
            name="ck_become_quest_mode",
        ),
        CheckConstraint(
            "expert_person_ref IS NULL OR learner_person_ref IS NULL "
            "OR expert_person_ref <> learner_person_ref",
            name="ck_become_quest_distinct_roles",
        ),
        Index("ix_become_quest_workspace_state", "workspace_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    quest_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    state: Mapped[str] = mapped_column(String(48), nullable=False)
    expert_person_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    learner_person_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BecomeQuestParticipantModel(Base):
    __tablename__ = "become_quest_participants"
    __table_args__ = (
        UniqueConstraint("quest_id", "role", name="uq_become_quest_participant_role"),
        UniqueConstraint(
            "quest_id", "person_ref", name="uq_become_quest_participant_person"
        ),
        CheckConstraint(
            "role IN ('EXPERT','LEARNER')",
            name="ck_become_quest_participant_role_value",
        ),
        CheckConstraint(
            "mode IN ('PRODUCTION','SIMULATED')",
            name="ck_become_quest_participant_mode",
        ),
        CheckConstraint(
            "mode = 'SIMULATED' OR person_ref NOT LIKE 'ref://simulated/%'",
            name="ck_become_quest_no_synthetic_production_participant",
        ),
        UniqueConstraint(
            "quest_id",
            "role",
            "person_ref",
            "active",
            name="uq_become_quest_participant_binding_state",
        ),
        ForeignKeyConstraint(
            ["quest_id", "mode"],
            ["become_quests.id", "become_quests.mode"],
            name="fk_become_quest_participant_quest_mode",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quests.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    person_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BecomeQuestArtifactModel(Base):
    __tablename__ = "become_quest_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "quest_id",
            "artifact_type",
            "artifact_version",
            name="uq_become_quest_artifact_version",
        ),
        CheckConstraint(
            "artifact_type IN ('TRANSCRIPT','EXPERT_PROFILE','PRODUCT','PROGRAM','EXERCISE')",
            name="ck_become_quest_artifact_type",
        ),
        CheckConstraint(
            "artifact_version > 0", name="ck_become_quest_artifact_version"
        ),
        CheckConstraint(
            "char_length(artifact_digest) = 64",
            name="ck_become_quest_artifact_digest",
        ),
        CheckConstraint(
            "artifact_type = 'TRANSCRIPT' OR "
            "(provider_settled = false AND transcript_available = false)",
            name="ck_become_quest_provider_transcript_only",
        ),
        CheckConstraint(
            "expert_approved = false",
            name="ck_become_quest_artifact_approval_is_receipt_only",
        ),
        UniqueConstraint(
            "quest_id", "id", name="uq_become_quest_artifact_quest_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quests.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_settled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    transcript_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    expert_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    expert_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BecomeQuestApprovalModel(Base):
    __tablename__ = "become_quest_approvals"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_become_quest_approval_artifact"),
        CheckConstraint(
            "approver_role = 'EXPERT'", name="ck_become_quest_approval_expert"
        ),
        CheckConstraint(
            "approval_state = 'APPROVED'", name="ck_become_quest_approval_state"
        ),
        CheckConstraint(
            "contract_version = 'become.role-correct-quest@1'",
            name="ck_become_quest_approval_contract",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_quest_approval_receipt_digest",
        ),
        ForeignKeyConstraint(
            ["quest_id", "artifact_id"],
            ["become_quest_artifacts.quest_id", "become_quest_artifacts.id"],
            name="fk_become_quest_approval_artifact_quest",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quests.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quest_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    approver_person_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    approver_role: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class BecomeQuestEnrollmentModel(Base):
    __tablename__ = "become_quest_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "quest_id",
            "invitation_version",
            name="uq_become_quest_enrollment_invitation_version",
        ),
        UniqueConstraint(
            "quest_id",
            "invitation_ref",
            name="uq_become_quest_enrollment_invitation_ref",
        ),
        CheckConstraint(
            "state IN ('INVITED','ENROLLED')",
            name="ck_become_quest_enrollment_state",
        ),
        CheckConstraint(
            "char_length(invitation_digest) = 64 AND "
            "(acceptance_digest IS NULL OR char_length(acceptance_digest) = 64)",
            name="ck_become_quest_enrollment_digests",
        ),
        CheckConstraint(
            "invitation_version > 0",
            name="ck_become_quest_enrollment_invitation_version",
        ),
        CheckConstraint(
            "learner_role = 'LEARNER' AND learner_active = true",
            name="ck_become_quest_enrollment_bound_learner",
        ),
        CheckConstraint(
            "(state = 'INVITED' AND acceptance_digest IS NULL AND accepted_at IS NULL) "
            "OR (state = 'ENROLLED' AND acceptance_digest IS NOT NULL "
            "AND accepted_at IS NOT NULL)",
            name="ck_become_quest_enrollment_acceptance_required",
        ),
        ForeignKeyConstraint(
            ["quest_id", "learner_role", "learner_person_ref", "learner_active"],
            [
                "become_quest_participants.quest_id",
                "become_quest_participants.role",
                "become_quest_participants.person_ref",
                "become_quest_participants.active",
            ],
            name="fk_become_quest_enrollment_bound_learner",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quests.id", ondelete="RESTRICT"), nullable=False
    )
    learner_person_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    learner_role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="LEARNER"
    )
    learner_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    invitation_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    invitation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    invitation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    acceptance_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BecomeQuestOperationAttemptModel(Base):
    __tablename__ = "become_quest_operation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "quest_id",
            "correlation_ref",
            "operation_type",
            "attempt_version",
            "idempotency_key",
            name="uq_become_quest_operation_attempt_scope",
        ),
        CheckConstraint("attempt_version > 0", name="ck_become_quest_attempt_version"),
        CheckConstraint(
            "state IN ('RESERVED','COMPLETED','FAILED','UNKNOWN','BLOCKED')",
            name="ck_become_quest_attempt_state",
        ),
        CheckConstraint(
            "char_length(request_digest) = 64 AND "
            "(receipt_digest IS NULL OR char_length(receipt_digest) = 64)",
            name="ck_become_quest_attempt_digests",
        ),
        Index("ix_become_quest_attempt_quest_operation", "quest_id", "operation_type"),
        ForeignKeyConstraint(
            ["quest_id", "correlation_ref"],
            ["become_quests.id", "become_quests.correlation_ref"],
            name="fk_become_quest_attempt_correlation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_quests.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
