"""Persist an exact one-shot S3 permit on its queued RU operation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0127_s3_operation_permit"
down_revision: str | None = "0126_personal_standalone_fence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "personal_command_operations",
        sa.Column("permit_approval_ref", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "personal_command_operations",
        sa.Column("permit_source_revision", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "personal_command_operations",
        sa.Column("permit_text_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "personal_command_operations",
        sa.Column("permit_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_personal_command_s3_permit_bundle",
        "personal_command_operations",
        "(permit_approval_ref IS NULL AND permit_source_revision IS NULL "
        "AND permit_text_sha256 IS NULL AND permit_expires_at IS NULL) OR "
        "(permit_approval_ref IS NOT NULL AND permit_source_revision IS NOT NULL "
        "AND permit_text_sha256 IS NOT NULL AND permit_expires_at IS NOT NULL "
        "AND length(permit_approval_ref) BETWEEN 1 AND 128 "
        "AND permit_source_revision ~ '^[0-9a-f]{40}$' "
        "AND permit_text_sha256 ~ '^[0-9a-f]{64}$')",
    )
    op.create_unique_constraint(
        "uq_personal_command_s3_permit_approval",
        "personal_command_operations",
        ["binding_id", "permit_approval_ref"],
    )


def downgrade() -> None:
    # A claimed/running or UNKNOWN operation may already represent a provider
    # effect. Refuse to erase the exact permit evidence until it is reconciled.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM personal_command_operations
                WHERE permit_approval_ref IS NOT NULL
                  AND state IN ('running', 'succeeded', 'unknown')
            ) THEN
                RAISE EXCEPTION 'S3 permit evidence requires reconciliation';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "uq_personal_command_s3_permit_approval",
        "personal_command_operations",
        type_="unique",
    )
    op.drop_constraint(
        "ck_personal_command_s3_permit_bundle",
        "personal_command_operations",
        type_="check",
    )
    op.drop_column("personal_command_operations", "permit_expires_at")
    op.drop_column("personal_command_operations", "permit_text_sha256")
    op.drop_column("personal_command_operations", "permit_source_revision")
    op.drop_column("personal_command_operations", "permit_approval_ref")
