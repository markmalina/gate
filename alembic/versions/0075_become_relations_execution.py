"""Persist canonical Relations consent execution receipts.

Revision ID: 0075_become_relations_execution
Revises: 0074_become_relations_delivery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075_become_relations_execution"
down_revision: str | None = "0074_become_relations_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_become_relations_handoff_acceptance",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("execution_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column(
            "execution_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("domain_event_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("domain_event_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "become_relations_consent_handoffs",
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE become_relations_consent_handoffs "
        "SET status = 'pending_execution' "
        "WHERE status = 'delivered'"
    )
    op.create_check_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        "status IN ('pending_delivery', 'pending_execution', 'executed')",
    )
    op.create_check_constraint(
        "ck_become_relations_handoff_execution",
        "become_relations_consent_handoffs",
        "("
        "status = 'pending_delivery' "
        "AND execution_digest IS NULL "
        "AND execution_payload IS NULL "
        "AND domain_event_id IS NULL "
        "AND domain_event_digest IS NULL "
        "AND executed_at IS NULL"
        ") OR ("
        "status = 'pending_execution' "
        "AND char_length(acceptance_digest) = 64 "
        "AND acceptance_payload IS NOT NULL "
        "AND delivered_at IS NOT NULL "
        "AND execution_digest IS NULL "
        "AND execution_payload IS NULL "
        "AND executed_at IS NULL"
        ") OR ("
        "status = 'executed' "
        "AND char_length(execution_digest) = 64 "
        "AND execution_payload IS NOT NULL "
        "AND domain_event_id LIKE 'consent_evt_%' "
        "AND char_length(domain_event_digest) = 64 "
        "AND executed_at IS NOT NULL"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_become_relations_handoff_execution",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.drop_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        type_="check",
    )
    op.execute(
        "UPDATE become_relations_consent_handoffs "
        "SET status = 'delivered' "
        "WHERE status IN ('pending_execution', 'executed')"
    )
    op.drop_column("become_relations_consent_handoffs", "executed_at")
    op.drop_column("become_relations_consent_handoffs", "domain_event_digest")
    op.drop_column("become_relations_consent_handoffs", "domain_event_id")
    op.drop_column("become_relations_consent_handoffs", "execution_payload")
    op.drop_column("become_relations_consent_handoffs", "execution_digest")
    op.create_check_constraint(
        "ck_become_relations_handoff_status",
        "become_relations_consent_handoffs",
        "status IN ('pending_delivery', 'delivered')",
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
