"""Metadata for the bounded Become Slice 3 learner invitation ledgers."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.domain import Base

become_slice3_learner_candidates = sa.Table(
    "become_slice3_learner_candidates",
    Base.metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("source_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("subject_ref", sa.String(128), nullable=False),
    sa.Column("telegram_actor_id", sa.String(64), nullable=True),
    sa.Column("private_dialog_id", sa.String(64), nullable=True),
    sa.Column("bot_installation_identity", sa.String(128), nullable=True),
    sa.Column("become_space_ref", sa.String(128), nullable=True),
    sa.Column("learner_binding_digest", sa.String(64), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["source_receipt_id"], ["telegram_receipts.id"], ondelete="RESTRICT"),
    sa.CheckConstraint(
        "state IN ('candidate','pinned','tombstoned')",
        name="ck_become_slice3_candidate_state",
    ),
    sa.CheckConstraint(
        "char_length(learner_binding_digest)=64",
        name="ck_become_slice3_candidate_digest",
    ),
    sa.UniqueConstraint(
        "workspace_id",
        "learner_binding_digest",
        name="uq_become_slice3_candidate_digest",
    ),
    sa.UniqueConstraint(
        "workspace_id",
        "telegram_actor_id",
        "private_dialog_id",
        "bot_installation_identity",
        "become_space_ref",
        name="uq_become_slice3_candidate_tuple",
    ),
)

become_slice3_pilot_slots = sa.Table(
    "become_slice3_pilot_slots",
    Base.metadata,
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("learner_binding_digest", sa.String(64), nullable=False),
    sa.Column("pinned_by_subject_ref", sa.String(128), nullable=False),
    sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    sa.CheckConstraint(
        "char_length(learner_binding_digest)=64", name="ck_become_slice3_slot_digest"
    ),
)

become_slice3_invitations = sa.Table(
    "become_slice3_invitations",
    Base.metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("expert_subject_ref", sa.String(128), nullable=False),
    sa.Column("learner_binding_digest", sa.String(64), nullable=False),
    sa.Column("become_space_ref", sa.String(128), nullable=False),
    sa.Column("bot_installation_identity", sa.String(128), nullable=False),
    sa.Column("approved_version", sa.Integer(), nullable=False),
    sa.Column("approved_digest", sa.String(64), nullable=False),
    sa.Column("consent_policy_version", sa.String(128), nullable=False),
    sa.Column("consent_policy_digest", sa.String(64), nullable=False),
    sa.Column("invitation_digest", sa.String(64), nullable=False),
    sa.Column("callback_ref", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("callback_digest", sa.String(64), nullable=False),
    sa.Column("state", sa.String(24), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(
        ["program_id"], ["become_pilot_program_drafts.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["candidate_id"],
        ["become_slice3_learner_candidates.id"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "state IN ('pending_delivery','delivered','accepted','declined','expired')",
        name="ck_become_slice3_invitation_state",
    ),
    sa.CheckConstraint("approved_version=2", name="ck_become_slice3_program_version"),
    sa.CheckConstraint(
        "char_length(approved_digest)=64 AND char_length(consent_policy_digest)=64 "
        "AND char_length(invitation_digest)=64 AND "
        "char_length(learner_binding_digest)=64 AND char_length(callback_digest)=64",
        name="ck_become_slice3_invitation_digests",
    ),
    sa.UniqueConstraint(
        "workspace_id", "invitation_digest", name="uq_become_slice3_invitation_tuple"
    ),
    sa.UniqueConstraint("callback_ref", name="uq_become_slice3_callback_ref"),
    sa.UniqueConstraint("callback_digest", name="uq_become_slice3_callback_digest"),
)
sa.Index(
    "ix_become_slice3_invitation_expiry",
    become_slice3_invitations.c.state,
    become_slice3_invitations.c.expires_at,
)

become_slice3_acceptances = sa.Table(
    "become_slice3_acceptances",
    Base.metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("source_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("consent_policy_version", sa.String(128), nullable=False),
    sa.Column("consent_policy_digest", sa.String(64), nullable=False),
    sa.Column("acceptance_digest", sa.String(64), nullable=False),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["invitation_id"], ["become_slice3_invitations.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(["source_receipt_id"], ["telegram_receipts.id"], ondelete="RESTRICT"),
    sa.CheckConstraint(
        "char_length(consent_policy_digest)=64 AND char_length(acceptance_digest)=64",
        name="ck_become_slice3_acceptance_digests",
    ),
    sa.UniqueConstraint("invitation_id", name="uq_become_slice3_acceptance_invitation"),
    sa.UniqueConstraint("acceptance_digest", name="uq_become_slice3_acceptance_digest"),
)

become_slice3_deliveries = sa.Table(
    "become_slice3_deliveries",
    Base.metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("kind", sa.String(24), nullable=False),
    sa.Column("request_digest", sa.String(64), nullable=False),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("telegram_message_id", sa.String(64), nullable=True),
    sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["invitation_id"], ["become_slice3_invitations.id"], ondelete="RESTRICT"
    ),
    sa.CheckConstraint(
        "kind IN ('invitation','confirmation')", name="ck_become_slice3_delivery_kind"
    ),
    sa.CheckConstraint(
        "outcome IN ('pending','reserved','sent','failed','unknown')",
        name="ck_become_slice3_delivery_outcome",
    ),
    sa.CheckConstraint("char_length(request_digest)=64", name="ck_become_slice3_delivery_digest"),
    sa.UniqueConstraint("invitation_id", "kind", name="uq_become_slice3_delivery_kind"),
    sa.UniqueConstraint("request_digest", name="uq_become_slice3_delivery_request"),
)

become_slice3_relations_outbox = sa.Table(
    "become_slice3_relations_outbox",
    Base.metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("acceptance_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("idempotency_key", sa.String(128), nullable=False),
    sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("command_digest", sa.String(64), nullable=False),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("receipt_digest", sa.String(64), nullable=True),
    sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["invitation_id"], ["become_slice3_invitations.id"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["acceptance_id"], ["become_slice3_acceptances.id"], ondelete="RESTRICT"
    ),
    sa.CheckConstraint(
        "outcome IN ('pending','reserved','executed','failed','unknown')",
        name="ck_become_slice3_relations_outcome",
    ),
    sa.CheckConstraint("char_length(command_digest)=64", name="ck_become_slice3_relations_digest"),
    sa.UniqueConstraint("acceptance_id", name="uq_become_slice3_relations_acceptance"),
    sa.UniqueConstraint("idempotency_key", name="uq_become_slice3_relations_key"),
    sa.UniqueConstraint("command_digest", name="uq_become_slice3_relations_command"),
)
