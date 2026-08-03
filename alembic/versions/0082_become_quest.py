"""Create durable role-correct BECOME Quest state.

Revision ID: 0082_become_quest
Revises: 0081_become_i4_recovery_v2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0082_become_quest"
down_revision: str | None = "0081_become_i4_recovery_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_quests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_ref", sa.String(length=160), nullable=False),
        sa.Column("correlation_ref", sa.String(length=160), nullable=False),
        sa.Column("contract_version", sa.String(length=96), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("expert_person_ref", sa.String(length=160), nullable=True),
        sa.Column("learner_person_ref", sa.String(length=160), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("source_provenance_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "contract_version = 'become.role-correct-quest@1'",
            name="ck_become_quest_contract",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','EXPERT_BOUND','EXPERT_CONSENTED','TRANSCRIPT_PENDING_REVIEW',"
            "'TRANSCRIPT_APPROVED','PRODUCT_PENDING_APPROVAL','PRODUCT_APPROVED',"
            "'LEARNER_INVITED','LEARNER_ENROLLED','READY_FOR_EXERCISE','BLOCKED','TERMINAL')",
            name="ck_become_quest_state",
        ),
        sa.CheckConstraint(
            "mode IN ('PRODUCTION','SIMULATED')", name="ck_become_quest_mode"
        ),
        sa.CheckConstraint(
            "expert_person_ref IS NULL OR learner_person_ref IS NULL OR expert_person_ref <> learner_person_ref",
            name="ck_become_quest_distinct_roles",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quest_ref", name="uq_become_quest_ref"),
        sa.UniqueConstraint("correlation_ref", name="uq_become_quest_correlation"),
    )
    op.create_index(
        "ix_become_quest_workspace_state", "become_quests", ["workspace_id", "state"]
    )

    op.create_table(
        "become_quest_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("person_ref", sa.String(length=160), nullable=False),
        sa.Column("provenance_digest", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('EXPERT','LEARNER')",
            name="ck_become_quest_participant_role_value",
        ),
        sa.CheckConstraint(
            "mode IN ('PRODUCTION','SIMULATED')",
            name="ck_become_quest_participant_mode",
        ),
        sa.CheckConstraint(
            "mode = 'SIMULATED' OR person_ref NOT LIKE 'ref://simulated/%'",
            name="ck_become_quest_no_synthetic_production_participant",
        ),
        sa.ForeignKeyConstraint(
            ["quest_id"], ["become_quests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quest_id", "role", name="uq_become_quest_participant_role"
        ),
        sa.UniqueConstraint(
            "quest_id", "person_ref", name="uq_become_quest_participant_person"
        ),
    )

    op.create_table(
        "become_quest_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("artifact_digest", sa.String(length=64), nullable=False),
        sa.Column("provider_settled", sa.Boolean(), nullable=False),
        sa.Column("transcript_available", sa.Boolean(), nullable=False),
        sa.Column("expert_reviewed", sa.Boolean(), nullable=False),
        sa.Column("expert_approved", sa.Boolean(), nullable=False),
        sa.Column("provenance_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "artifact_type IN ('TRANSCRIPT','EXPERT_PROFILE','PRODUCT','PROGRAM','EXERCISE')",
            name="ck_become_quest_artifact_type",
        ),
        sa.CheckConstraint(
            "artifact_version > 0", name="ck_become_quest_artifact_version"
        ),
        sa.CheckConstraint(
            "char_length(artifact_digest) = 64", name="ck_become_quest_artifact_digest"
        ),
        sa.CheckConstraint(
            "artifact_type = 'TRANSCRIPT' OR (provider_settled = false AND transcript_available = false)",
            name="ck_become_quest_provider_transcript_only",
        ),
        sa.ForeignKeyConstraint(
            ["quest_id"], ["become_quests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quest_id",
            "artifact_type",
            "artifact_version",
            name="uq_become_quest_artifact_version",
        ),
    )

    op.create_table(
        "become_quest_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_person_ref", sa.String(length=160), nullable=False),
        sa.Column("approver_role", sa.String(length=16), nullable=False),
        sa.Column("approval_state", sa.String(length=16), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(length=96), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("receipt_payload", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "approver_role = 'EXPERT'", name="ck_become_quest_approval_expert"
        ),
        sa.CheckConstraint(
            "approval_state = 'APPROVED'", name="ck_become_quest_approval_state"
        ),
        sa.CheckConstraint(
            "contract_version = 'become.role-correct-quest@1'",
            name="ck_become_quest_approval_contract",
        ),
        sa.CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_quest_approval_receipt_digest",
        ),
        sa.ForeignKeyConstraint(
            ["quest_id"], ["become_quests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["become_quest_artifacts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", name="uq_become_quest_approval_artifact"),
    )

    op.create_table(
        "become_quest_enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learner_person_ref", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("invitation_digest", sa.String(length=64), nullable=False),
        sa.Column("acceptance_digest", sa.String(length=64), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('INVITED','ENROLLED')", name="ck_become_quest_enrollment_state"
        ),
        sa.CheckConstraint(
            "char_length(invitation_digest) = 64 AND "
            "(acceptance_digest IS NULL OR char_length(acceptance_digest) = 64)",
            name="ck_become_quest_enrollment_digests",
        ),
        sa.ForeignKeyConstraint(
            ["quest_id"], ["become_quests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quest_id", name="uq_become_quest_enrollment"),
    )

    op.create_table(
        "become_quest_operation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_ref", sa.String(length=160), nullable=False),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("attempt_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("receipt_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_version > 0", name="ck_become_quest_attempt_version"
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED','COMPLETED','FAILED','UNKNOWN','BLOCKED')",
            name="ck_become_quest_attempt_state",
        ),
        sa.CheckConstraint(
            "char_length(request_digest) = 64 AND "
            "(receipt_digest IS NULL OR char_length(receipt_digest) = 64)",
            name="ck_become_quest_attempt_digests",
        ),
        sa.ForeignKeyConstraint(
            ["quest_id"], ["become_quests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quest_id",
            "correlation_ref",
            "operation_type",
            "attempt_version",
            name="uq_become_quest_operation_attempt_scope",
        ),
    )
    op.create_index(
        "ix_become_quest_attempt_quest_operation",
        "become_quest_operation_attempts",
        ["quest_id", "operation_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_quest_attempt_quest_operation",
        table_name="become_quest_operation_attempts",
    )
    op.drop_table("become_quest_operation_attempts")
    op.drop_table("become_quest_enrollments")
    op.drop_table("become_quest_approvals")
    op.drop_table("become_quest_artifacts")
    op.drop_table("become_quest_participants")
    op.drop_index("ix_become_quest_workspace_state", table_name="become_quests")
    op.drop_table("become_quests")
