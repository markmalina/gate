"""Add durable provider-disabled Relations delivery receipts.

Revision ID: 0074_become_relations_delivery
Revises: 0073_become_relations_handoff
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074_become_relations_delivery"
down_revision: str | None = "0073_become_relations_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("acceptance_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column(
            "acceptance_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        "status IN ('pending_delivery', 'delivered')",
    )
    op.create_check_constraint(
        "ck_become_relations_handoff_attempts",
        "become_relations_consent_handoffs",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_become_relations_handoff_acceptance",
        "become_relations_consent_handoffs",
        "("
        "status = 'pending_delivery' "
        "AND acceptance_digest IS NULL "
        "AND acceptance_payload IS NULL "
        "AND delivered_at IS NULL"
        ") OR ("
        "status = 'delivered' "
        "AND char_length(acceptance_digest) = 64 "
        "AND acceptance_payload IS NOT NULL "
        "AND delivered_at IS NOT NULL"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_become_relations_handoff_acceptance",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_relations_handoff_attempts",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.drop_column("become_relations_consent_handoffs", "delivered_at")
    op.drop_column("become_relations_consent_handoffs", "last_error_code")
    op.drop_column("become_relations_consent_handoffs", "acceptance_payload")
    op.drop_column("become_relations_consent_handoffs", "acceptance_digest")
    op.drop_column("become_relations_consent_handoffs", "attempt_count")
    op.create_check_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        "status = 'pending_delivery'",
    )
