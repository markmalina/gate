"""Enforce exact workspace, connector, binding and event scope for Beta.

Revision ID: 0097_beta_exact_scope
Revises: 0096_beta_binding_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097_beta_exact_scope"
down_revision: str | Sequence[str] | None = "0096_beta_binding_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_beta_space_event_id_binding_workspace",
        "beta_space_events",
        ["id", "binding_id", "workspace_id"],
    )
    for table, name in (
        ("beta_space_replies", "fk_beta_space_reply_event_scope"),
        ("beta_consent_deliveries", "fk_beta_consent_delivery_event_scope"),
        ("beta_material_jobs", "fk_beta_material_job_event_scope"),
        ("beta_wedding_voice_jobs", "fk_beta_wedding_voice_job_event_scope"),
        ("beta_space_work_items", "fk_beta_space_work_item_event_scope"),
    ):
        op.create_foreign_key(
            name,
            table,
            "beta_space_events",
            ["event_id", "binding_id", "workspace_id"],
            ["id", "binding_id", "workspace_id"],
            ondelete="RESTRICT",
        )
    op.create_check_constraint(
        "ck_beta_space_work_item_scope",
        "beta_space_work_items",
        "(event_id IS NULL AND binding_id IS NULL AND workspace_id IS NULL) OR "
        "(event_id IS NOT NULL AND binding_id IS NOT NULL "
        "AND workspace_id IS NOT NULL)",
    )

    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column("workspace_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "beta_checkpoint_dispatch_intents",
        sa.Column("binding_id", sa.UUID(), nullable=True),
    )
    connection = op.get_bind()
    ambiguous = connection.execute(
        sa.text(
            "SELECT count(*) FROM beta_checkpoint_dispatch_intents AS intent "
            "WHERE (SELECT count(*) FROM beta_space_bindings AS binding "
            "WHERE binding.space_ref = intent.space_id) <> 1"
        )
    ).scalar_one()
    if ambiguous:
        raise RuntimeError("beta_checkpoint_dispatch_legacy_scope_ambiguous")
    connection.execute(
        sa.text(
            "UPDATE beta_checkpoint_dispatch_intents AS intent "
            "SET workspace_id = binding.workspace_id, binding_id = binding.id "
            "FROM beta_space_bindings AS binding "
            "WHERE binding.space_ref = intent.space_id"
        )
    )
    unresolved = connection.execute(
        sa.text(
            "SELECT count(*) FROM beta_checkpoint_dispatch_intents "
            "WHERE workspace_id IS NULL OR binding_id IS NULL"
        )
    ).scalar_one()
    if unresolved:
        raise RuntimeError("beta_checkpoint_dispatch_scope_unresolved")
    duplicate_scope = connection.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "SELECT workspace_id, binding_id FROM beta_checkpoint_dispatch_intents "
            "GROUP BY workspace_id, binding_id HAVING count(*) > 1"
            ") AS duplicates"
        )
    ).scalar_one()
    if duplicate_scope:
        raise RuntimeError("beta_checkpoint_dispatch_scope_duplicate")

    op.drop_constraint(
        "beta_checkpoint_dispatch_intents_pkey",
        "beta_checkpoint_dispatch_intents",
        type_="primary",
    )
    op.alter_column(
        "beta_checkpoint_dispatch_intents", "workspace_id", nullable=False
    )
    op.alter_column(
        "beta_checkpoint_dispatch_intents", "binding_id", nullable=False
    )
    op.create_primary_key(
        "pk_beta_checkpoint_dispatch_scope",
        "beta_checkpoint_dispatch_intents",
        ["workspace_id", "binding_id"],
    )
    op.create_foreign_key(
        "fk_beta_checkpoint_dispatch_binding_workspace",
        "beta_checkpoint_dispatch_intents",
        "beta_space_bindings",
        ["binding_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_space = connection.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "SELECT space_id FROM beta_checkpoint_dispatch_intents "
            "GROUP BY space_id HAVING count(*) > 1"
            ") AS duplicates"
        )
    ).scalar_one()
    if duplicate_space:
        raise RuntimeError("beta_checkpoint_dispatch_space_collision_blocks_downgrade")

    op.drop_constraint(
        "fk_beta_checkpoint_dispatch_binding_workspace",
        "beta_checkpoint_dispatch_intents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "pk_beta_checkpoint_dispatch_scope",
        "beta_checkpoint_dispatch_intents",
        type_="primary",
    )
    op.create_primary_key(
        "beta_checkpoint_dispatch_intents_pkey",
        "beta_checkpoint_dispatch_intents",
        ["space_id"],
    )
    op.drop_column("beta_checkpoint_dispatch_intents", "binding_id")
    op.drop_column("beta_checkpoint_dispatch_intents", "workspace_id")

    op.drop_constraint(
        "ck_beta_space_work_item_scope",
        "beta_space_work_items",
        type_="check",
    )
    for table, name in reversed(
        (
            ("beta_space_replies", "fk_beta_space_reply_event_scope"),
            ("beta_consent_deliveries", "fk_beta_consent_delivery_event_scope"),
            ("beta_material_jobs", "fk_beta_material_job_event_scope"),
            ("beta_wedding_voice_jobs", "fk_beta_wedding_voice_job_event_scope"),
            ("beta_space_work_items", "fk_beta_space_work_item_event_scope"),
        )
    ):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint(
        "uq_beta_space_event_id_binding_workspace",
        "beta_space_events",
        type_="unique",
    )
