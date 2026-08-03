"""Correct role-correct BECOME Quest durability invariants.

Revision ID: 0083_become_quest_corrective
Revises: 0082_become_quest
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_become_quest_corrective"
down_revision: str | None = "0082_become_quest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_become_quest_correlation",
        "become_quests",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_id_correlation",
        "become_quests",
        ["id", "correlation_ref"],
    )
    op.create_unique_constraint(
        "uq_become_quest_id_mode",
        "become_quests",
        ["id", "mode"],
    )

    op.create_unique_constraint(
        "uq_become_quest_participant_binding_state",
        "become_quest_participants",
        ["quest_id", "role", "person_ref", "active"],
    )
    op.create_foreign_key(
        "fk_become_quest_participant_quest_mode",
        "become_quest_participants",
        "become_quests",
        ["quest_id", "mode"],
        ["id", "mode"],
        ondelete="RESTRICT",
    )

    op.create_unique_constraint(
        "uq_become_quest_artifact_quest_id",
        "become_quest_artifacts",
        ["quest_id", "id"],
    )
    op.create_check_constraint(
        "ck_become_quest_artifact_approval_is_receipt_only",
        "become_quest_artifacts",
        "expert_approved = false",
    )
    op.create_foreign_key(
        "fk_become_quest_approval_artifact_quest",
        "become_quest_approvals",
        "become_quest_artifacts",
        ["quest_id", "artifact_id"],
        ["quest_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "become_quest_approvals",
        sa.Column("approver_active", sa.Boolean(), nullable=True),
    )
    op.execute("UPDATE become_quest_approvals SET approver_active = true")
    op.alter_column(
        "become_quest_approvals",
        "approver_active",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_become_quest_approval_active",
        "become_quest_approvals",
        "approver_active = true",
    )
    op.create_foreign_key(
        "fk_become_quest_approval_bound_expert",
        "become_quest_approvals",
        "become_quest_participants",
        ["quest_id", "approver_role", "approver_person_ref", "approver_active"],
        ["quest_id", "role", "person_ref", "active"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "become_quest_enrollments",
        sa.Column("learner_role", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "become_quest_enrollments",
        sa.Column("learner_active", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "become_quest_enrollments",
        sa.Column("invitation_ref", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "become_quest_enrollments",
        sa.Column("invitation_version", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE become_quest_enrollments SET "
        "learner_role = 'LEARNER', learner_active = true, "
        "invitation_ref = 'ref://migration/invitation/' || id::text, "
        "invitation_version = 1"
    )
    op.alter_column("become_quest_enrollments", "learner_role", nullable=False)
    op.alter_column("become_quest_enrollments", "learner_active", nullable=False)
    op.alter_column("become_quest_enrollments", "invitation_ref", nullable=False)
    op.alter_column("become_quest_enrollments", "invitation_version", nullable=False)
    op.drop_constraint(
        "uq_become_quest_enrollment",
        "become_quest_enrollments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_enrollment_invitation_version",
        "become_quest_enrollments",
        ["quest_id", "invitation_version"],
    )
    op.create_unique_constraint(
        "uq_become_quest_enrollment_invitation_ref",
        "become_quest_enrollments",
        ["quest_id", "invitation_ref"],
    )
    op.create_check_constraint(
        "ck_become_quest_enrollment_invitation_version",
        "become_quest_enrollments",
        "invitation_version > 0",
    )
    op.create_check_constraint(
        "ck_become_quest_enrollment_bound_learner",
        "become_quest_enrollments",
        "learner_role = 'LEARNER' AND learner_active = true",
    )
    op.create_check_constraint(
        "ck_become_quest_enrollment_acceptance_required",
        "become_quest_enrollments",
        "(state = 'INVITED' AND acceptance_digest IS NULL AND accepted_at IS NULL) "
        "OR (state = 'ENROLLED' AND acceptance_digest IS NOT NULL "
        "AND accepted_at IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_become_quest_enrollment_bound_learner",
        "become_quest_enrollments",
        "become_quest_participants",
        ["quest_id", "learner_role", "learner_person_ref", "learner_active"],
        ["quest_id", "role", "person_ref", "active"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "become_quest_operation_attempts",
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
    )
    op.execute(
        "UPDATE become_quest_operation_attempts "
        "SET idempotency_key = 'migrated:' || id::text"
    )
    op.alter_column(
        "become_quest_operation_attempts",
        "idempotency_key",
        nullable=False,
    )
    op.drop_constraint(
        "uq_become_quest_operation_attempt_scope",
        "become_quest_operation_attempts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_operation_attempt_scope",
        "become_quest_operation_attempts",
        [
            "quest_id",
            "correlation_ref",
            "operation_type",
            "attempt_version",
            "idempotency_key",
        ],
    )
    op.create_foreign_key(
        "fk_become_quest_attempt_correlation",
        "become_quest_operation_attempts",
        "become_quests",
        ["quest_id", "correlation_ref"],
        ["id", "correlation_ref"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Data-bearing downgrade is not a safe production rollback. This mechanical
    # downgrade exists only for empty-schema development checks.
    op.drop_constraint(
        "fk_become_quest_attempt_correlation",
        "become_quest_operation_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_become_quest_operation_attempt_scope",
        "become_quest_operation_attempts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_operation_attempt_scope",
        "become_quest_operation_attempts",
        ["quest_id", "correlation_ref", "operation_type", "attempt_version"],
    )
    op.drop_column("become_quest_operation_attempts", "idempotency_key")

    op.drop_constraint(
        "fk_become_quest_enrollment_bound_learner",
        "become_quest_enrollments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_become_quest_enrollment_acceptance_required",
        "become_quest_enrollments",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_quest_enrollment_bound_learner",
        "become_quest_enrollments",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_quest_enrollment_invitation_version",
        "become_quest_enrollments",
        type_="check",
    )
    op.drop_constraint(
        "uq_become_quest_enrollment_invitation_ref",
        "become_quest_enrollments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_become_quest_enrollment_invitation_version",
        "become_quest_enrollments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_enrollment",
        "become_quest_enrollments",
        ["quest_id"],
    )
    op.drop_column("become_quest_enrollments", "invitation_version")
    op.drop_column("become_quest_enrollments", "invitation_ref")
    op.drop_column("become_quest_enrollments", "learner_active")
    op.drop_column("become_quest_enrollments", "learner_role")

    op.drop_constraint(
        "fk_become_quest_approval_bound_expert",
        "become_quest_approvals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_become_quest_approval_active",
        "become_quest_approvals",
        type_="check",
    )
    op.drop_column("become_quest_approvals", "approver_active")

    op.drop_constraint(
        "fk_become_quest_approval_artifact_quest",
        "become_quest_approvals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_become_quest_artifact_approval_is_receipt_only",
        "become_quest_artifacts",
        type_="check",
    )
    op.drop_constraint(
        "uq_become_quest_artifact_quest_id",
        "become_quest_artifacts",
        type_="unique",
    )
    op.drop_constraint(
        "fk_become_quest_participant_quest_mode",
        "become_quest_participants",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_become_quest_participant_binding_state",
        "become_quest_participants",
        type_="unique",
    )
    op.drop_constraint(
        "uq_become_quest_id_mode",
        "become_quests",
        type_="unique",
    )
    op.drop_constraint(
        "uq_become_quest_id_correlation",
        "become_quests",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_become_quest_correlation",
        "become_quests",
        ["correlation_ref"],
    )
