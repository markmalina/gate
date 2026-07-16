"""Module-owned persistence for the standalone Relationship CRM core."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class RelationshipSpaceModel(Base):
    __tablename__ = "relationship_crm_spaces"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "space_key",
            name="uq_relationship_crm_spaces_workspace_key",
        ),
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_relationship_crm_spaces_id_workspace",
        ),
        CheckConstraint(
            "kind IN ('personal', 'commercial', 'program')",
            name="ck_relationship_crm_spaces_kind",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_spaces_name",
        ),
        Index("ix_relationship_crm_spaces_workspace_kind", "workspace_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    space_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationshipPersonModel(Base):
    __tablename__ = "relationship_crm_people"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "person_key",
            name="uq_relationship_crm_people_workspace_key",
        ),
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_relationship_crm_people_id_workspace",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 255",
            name="ck_relationship_crm_people_display_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    person_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationshipModel(Base):
    __tablename__ = "relationship_crm_relationships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "workspace_id"],
            ["relationship_crm_spaces.id", "relationship_crm_spaces.workspace_id"],
            name="fk_relationship_crm_relationships_space_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["person_id", "workspace_id"],
            ["relationship_crm_people.id", "relationship_crm_people.workspace_id"],
            name="fk_relationship_crm_relationships_person_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "relationship_key",
            name="uq_relationship_crm_relationships_space_key",
        ),
        UniqueConstraint(
            "id",
            "space_id",
            "person_id",
            "workspace_id",
            name="uq_relationship_crm_relationships_id_scope",
        ),
        CheckConstraint(
            "role IN ('personal_contact', 'family', 'friend', 'lead', 'customer', "
            "'partner', 'contractor', 'team_member', 'participant', "
            "'program_member', 'mentor', 'other')",
            name="ck_relationship_crm_relationships_role",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= created_at",
            name="ck_relationship_crm_relationships_dates",
        ),
        Index(
            "ix_relationship_crm_relationships_space_role",
            "workspace_id",
            "space_id",
            "role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relationship_key: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RelationshipInteractionModel(Base):
    __tablename__ = "relationship_crm_interactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_interactions_relationship_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "interaction_key",
            name="uq_relationship_crm_interactions_space_key",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "source_ref",
            name="uq_relationship_crm_interactions_source",
        ),
        CheckConstraint(
            "kind IN ('message', 'call', 'meeting', 'payment', 'agreement', "
            "'program_event', 'other')",
            name="ck_relationship_crm_interactions_kind",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_relationship_crm_interactions_direction",
        ),
        CheckConstraint(
            "summary IS NULL OR char_length(summary) BETWEEN 1 AND 4096",
            name="ck_relationship_crm_interactions_summary",
        ),
        Index(
            "ix_relationship_crm_interactions_timeline",
            "workspace_id",
            "space_id",
            "person_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relationship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    interaction_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
