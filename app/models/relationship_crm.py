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
    Integer,
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


class RelationshipChannelIdentityModel(Base):
    __tablename__ = "relationship_crm_channel_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["person_id", "workspace_id"],
            ["relationship_crm_people.id", "relationship_crm_people.workspace_id"],
            name="fk_relationship_crm_identities_person_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "identity_key",
            name="uq_relationship_crm_identities_workspace_key",
        ),
        UniqueConstraint(
            "workspace_id",
            "channel",
            "connector_ref",
            "external_subject_ref",
            name="uq_relationship_crm_identities_external",
        ),
        CheckConstraint(
            "channel IN ('telegram', 'email', 'phone', 'other')",
            name="ck_relationship_crm_identities_channel",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    external_subject_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationshipAttentionModel(Base):
    __tablename__ = "relationship_crm_attention"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_attention_relationship_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "conversation_ref",
            name="uq_relationship_crm_attention_conversation",
        ),
        CheckConstraint(
            "state IN ('untriaged', 'action_on_me', 'waiting_on_them', "
            "'scheduled', 'no_action', 'archived')",
            name="ck_relationship_crm_attention_state",
        ),
        CheckConstraint("revision > 0", name="ck_relationship_crm_attention_revision"),
        CheckConstraint(
            "(state = 'scheduled' AND next_review_at IS NOT NULL) "
            "OR state <> 'scheduled'",
            name="ck_relationship_crm_attention_scheduled_review",
        ),
        Index(
            "ix_relationship_crm_attention_review",
            "workspace_id",
            "space_id",
            "state",
            "next_review_at",
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
    conversation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_key: Mapped[str] = mapped_column(String(128), nullable=False)
    last_command_key: Mapped[str] = mapped_column(String(128), nullable=False)
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationshipNoteModel(Base):
    __tablename__ = "relationship_crm_notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_notes_relationship_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "note_key",
            name="uq_relationship_crm_notes_space_key",
        ),
        CheckConstraint(
            "kind IN ('note', 'thought')",
            name="ck_relationship_crm_notes_kind",
        ),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 16384",
            name="ck_relationship_crm_notes_body",
        ),
        Index(
            "ix_relationship_crm_notes_person_created",
            "workspace_id",
            "space_id",
            "person_id",
            "created_at",
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
    note_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    actor_key: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationshipWorkItemModel(Base):
    __tablename__ = "relationship_crm_work_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relationship_id", "space_id", "person_id", "workspace_id"],
            [
                "relationship_crm_relationships.id",
                "relationship_crm_relationships.space_id",
                "relationship_crm_relationships.person_id",
                "relationship_crm_relationships.workspace_id",
            ],
            name="fk_relationship_crm_work_items_relationship_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "space_id",
            "work_item_key",
            name="uq_relationship_crm_work_items_space_key",
        ),
        CheckConstraint(
            "responsible_party IN ('workspace_owner', 'person', 'shared', 'unassigned')",
            name="ck_relationship_crm_work_items_responsible",
        ),
        CheckConstraint(
            "status IN ('open', 'done', 'cancelled')",
            name="ck_relationship_crm_work_items_status",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_relationship_crm_work_items_dates",
        ),
        Index(
            "ix_relationship_crm_work_items_review",
            "workspace_id",
            "space_id",
            "status",
            "next_review_at",
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
    work_item_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    responsible_party: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    next_review_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
