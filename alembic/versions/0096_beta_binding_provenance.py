"""Persist Beta binding actor policy and quarantine unproved Wedding provenance.

Revision ID: 0096_beta_binding_provenance
Revises: 0095_beta_checkpoint_wake
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096_beta_binding_provenance"
down_revision: str | Sequence[str] | None = "0095_beta_checkpoint_wake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WEDDING_TENANT_REF = "beta-user-mark-wedding-001"


def upgrade() -> None:
    op.add_column(
        "beta_space_bindings",
        sa.Column(
            "actor_policy",
            sa.String(32),
            nullable=False,
            server_default="subject_only",
        ),
    )
    op.add_column(
        "beta_space_bindings",
        sa.Column(
            "provenance_state",
            sa.String(32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "beta_space_bindings",
        sa.Column(
            "provenance_evidence_kind",
            sa.String(64),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.drop_constraint(
        "ck_beta_space_binding_state", "beta_space_bindings", type_="check"
    )
    op.drop_constraint(
        "ck_beta_space_binding_consented_at", "beta_space_bindings", type_="check"
    )
    op.execute(
        sa.text(
            "UPDATE beta_space_bindings SET "
            "actor_policy = 'group_members', "
            "provenance_state = 'quarantined', "
            "provenance_evidence_kind = 'legacy_mutable_telegram', "
            "state = 'quarantined' "
            "WHERE tenant_ref = :tenant_ref"
        ).bindparams(tenant_ref=_WEDDING_TENANT_REF)
    )
    op.create_check_constraint(
        "ck_beta_space_binding_state",
        "beta_space_bindings",
        "state IN ('ready','consented','revoked','quarantined')",
    )
    op.create_check_constraint(
        "ck_beta_space_binding_consented_at",
        "beta_space_bindings",
        "(state = 'consented' AND consented_at IS NOT NULL) OR "
        "(state = 'quarantined') OR "
        "(state NOT IN ('consented','quarantined') AND consented_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_beta_space_binding_actor_policy",
        "beta_space_bindings",
        "actor_policy IN ('subject_only','group_members')",
    )
    op.create_check_constraint(
        "ck_beta_space_binding_provenance_state",
        "beta_space_bindings",
        "provenance_state IN ('not_applicable','verified','quarantined')",
    )
    op.create_check_constraint(
        "ck_beta_space_binding_provenance_evidence",
        "beta_space_bindings",
        "(provenance_state = 'not_applicable' AND "
        "provenance_evidence_kind = 'not_applicable') OR "
        "(provenance_state = 'verified' AND "
        "provenance_evidence_kind = 'authenticated_control_plane') OR "
        "(provenance_state = 'quarantined' AND "
        "provenance_evidence_kind = 'legacy_mutable_telegram')",
    )


def downgrade() -> None:
    # Never restore an unproved legacy binding to active during rollback.
    op.execute(
        sa.text(
            "UPDATE beta_space_bindings SET state = 'revoked', consented_at = NULL "
            "WHERE provenance_state = 'quarantined'"
        )
    )
    op.drop_constraint(
        "ck_beta_space_binding_provenance_evidence",
        "beta_space_bindings",
        type_="check",
    )
    op.drop_constraint(
        "ck_beta_space_binding_provenance_state",
        "beta_space_bindings",
        type_="check",
    )
    op.drop_constraint(
        "ck_beta_space_binding_actor_policy", "beta_space_bindings", type_="check"
    )
    op.drop_constraint(
        "ck_beta_space_binding_consented_at", "beta_space_bindings", type_="check"
    )
    op.drop_constraint(
        "ck_beta_space_binding_state", "beta_space_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_beta_space_binding_state",
        "beta_space_bindings",
        "state IN ('ready','consented','revoked')",
    )
    op.create_check_constraint(
        "ck_beta_space_binding_consented_at",
        "beta_space_bindings",
        "(state = 'consented' AND consented_at IS NOT NULL) OR "
        "(state != 'consented' AND consented_at IS NULL)",
    )
    op.drop_column("beta_space_bindings", "provenance_evidence_kind")
    op.drop_column("beta_space_bindings", "provenance_state")
    op.drop_column("beta_space_bindings", "actor_policy")
