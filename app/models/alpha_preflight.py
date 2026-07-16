"""Persistence for one delivery-free Alpha provider preflight."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AlphaModelPreflightModel(Base):
    """One idempotent provider attempt with no group content or delivery authority."""

    __tablename__ = "alpha_model_preflights"
    __table_args__ = (
        ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_alpha_preflight_deployment_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_alpha_preflight_revision_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "preflight_key",
            name="uq_alpha_model_preflight_key",
        ),
        Index(
            "ix_alpha_model_preflight_workspace_created",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "state IN ('reserved', 'succeeded', 'failed')",
            name="ck_alpha_model_preflight_state",
        ),
        CheckConstraint(
            "reserved_tokens > 0",
            name="ck_alpha_model_preflight_reserved_tokens",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_alpha_model_preflight_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_alpha_model_preflight_output_tokens",
        ),
        CheckConstraint(
            "result_kind IS NULL OR result_kind IN ('reply', 'handoff', 'no_reply')",
            name="ck_alpha_model_preflight_result_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instruction_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    preflight_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'reserved'"),
    )
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
