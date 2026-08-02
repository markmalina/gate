"""Persistence models for the isolated BECOME controlled intake."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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


class BecomeControlledIntakeReceiptModel(Base):
    """One content-free receipt bound to a durable Telegram update."""

    __tablename__ = "become_controlled_intake_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_intake_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_intake_workspace_key",
        ),
        UniqueConstraint(
            "source_receipt_id",
            "intent",
            name="uq_become_intake_source_intent",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-controlled-intake@1'",
            name="ck_become_intake_schema",
        ),
        CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_intake_connector",
        ),
        CheckConstraint(
            "intent = 'registration_confirmed'",
            name="ck_become_intake_intent",
        ),
        CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_intake_owner_only",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_intake_digest",
        ),
        Index(
            "ix_become_intake_installation_created",
            "installation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    update_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    group_state: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeControlledConsentReceiptModel(Base):
    """One explicit content-free consent receipt after controlled registration."""

    __tablename__ = "become_controlled_consent_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_consent_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_consent_workspace_key",
        ),
        UniqueConstraint(
            "source_receipt_id",
            name="uq_become_consent_source_receipt",
        ),
        UniqueConstraint(
            "installation_id",
            "subject_ref",
            "policy_version",
            "intent",
            name="uq_become_consent_subject_policy",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-controlled-consent-intake@1'",
            name="ck_become_consent_schema",
        ),
        CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_consent_connector",
        ),
        CheckConstraint(
            "intent = 'consent_accepted'",
            name="ck_become_consent_intent",
        ),
        CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_consent_owner_only",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64 AND char_length(policy_digest) = 64",
            name="ck_become_consent_digests",
        ),
        Index(
            "ix_become_consent_installation_created",
            "installation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    quest_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    correlation_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    action_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    group_state: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeRelationsConsentHandoffModel(Base):
    """Provider-disabled Relations command projected from one consent receipt."""

    __tablename__ = "become_relations_consent_handoffs"
    __table_args__ = (
        UniqueConstraint(
            "consent_receipt_id",
            name="uq_become_relations_handoff_consent",
        ),
        UniqueConstraint(
            "workspace_id",
            "handoff_key",
            name="uq_become_relations_handoff_workspace_key",
        ),
        CheckConstraint(
            "schema_name = 'relations.controlled-consent-command@1'",
            name="ck_become_relations_handoff_schema",
        ),
        CheckConstraint(
            "status IN ('pending_delivery', 'delivered')",
            name="ck_become_relations_handoff_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_become_relations_handoff_attempts",
        ),
        CheckConstraint(
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
            name="ck_become_relations_handoff_acceptance",
        ),
        CheckConstraint(
            "char_length(command_digest) = 64",
            name="ck_become_relations_handoff_digest",
        ),
        Index(
            "ix_become_relations_handoff_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_controlled_consent_receipts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    handoff_key: Mapped[str] = mapped_column(String(128), nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    acceptance_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    acceptance_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
