"""Durable opaque-only ledger for the disabled AQUB delivery v2 seam."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AQUBV2DeliveryLedger(Base):
    __tablename__ = "aqub_v2_delivery_ledger"
    __table_args__ = (
        UniqueConstraint("installation_ref", "idempotency_key", name="uq_aqub_v2_delivery_key"),
        UniqueConstraint(
            "consent_cancellation_id",
            name="uq_aqub_v2_delivery_consent_cancellation_id",
        ),
        UniqueConstraint(
            "consent_cancellation_ref",
            name="uq_aqub_v2_delivery_consent_cancellation_ref",
        ),
        CheckConstraint("route_generation > 0", name="ck_aqub_v2_delivery_generation"),
        CheckConstraint("char_length(command_sha256)=64", name="ck_aqub_v2_command_sha"),
        CheckConstraint(
            "command_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_aqub_v2_delivery_command_sha_hex",
        ),
        CheckConstraint(
            "installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_delivery_installation_ref",
        ),
        CheckConstraint(
            "conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_delivery_conversation_ref",
        ),
        CheckConstraint(
            "cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_delivery_cube_ref",
        ),
        CheckConstraint(
            "idempotency_key ~ '^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$'",
            name="ck_aqub_v2_delivery_idempotency_key",
        ),
        CheckConstraint(
            "ack_ref ~ '^ack:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_delivery_ack_ref",
        ),
        CheckConstraint(
            "state IN ('committed','sending','sent','failed','unknown','rejected')",
            name="ck_aqub_v2_delivery_state",
        ),
        CheckConstraint(
            "(content_redacted_at IS NULL AND conversation_ref IS NOT NULL "
            "AND cube_ref IS NOT NULL AND command_canonical IS NOT NULL "
            "AND command_json IS NOT NULL AND ack_ref IS NOT NULL "
            "AND lifecycle_policy_version IS NULL "
            "AND lifecycle_consent_ref IS NULL "
            "AND lifecycle_receipt_ref IS NULL) OR "
            "(content_redacted_at IS NOT NULL AND conversation_ref IS NULL "
            "AND cube_ref IS NULL AND command_canonical IS NULL "
            "AND command_json IS NULL AND ack_ref IS NULL "
            "AND receipt_json IS NULL AND lifecycle_policy_version IS NOT NULL "
            "AND lifecycle_consent_ref IS NOT NULL "
            "AND lifecycle_receipt_ref IS NOT NULL)",
            name="ck_aqub_v2_delivery_lifecycle_content",
        ),
        CheckConstraint(
            "(consent_cancellation_id IS NULL "
            "AND consent_cancellation_ref IS NULL "
            "AND revoked_consent_ref IS NULL "
            "AND revoked_authority_epoch IS NULL "
            "AND consent_cancellation_receipt IS NULL "
            "AND consent_revoked_at IS NULL) OR "
            "(consent_cancellation_id IS NOT NULL "
            "AND consent_cancellation_ref IS NOT NULL "
            "AND revoked_consent_ref IS NOT NULL "
            "AND revoked_authority_epoch IS NOT NULL "
            "AND revoked_authority_epoch > 0 "
            "AND consent_cancellation_receipt IS NOT NULL "
            "AND consent_revoked_at IS NOT NULL "
            "AND state IN ('rejected','unknown','sent','failed') "
            "AND (settled_at IS NOT NULL OR content_redacted_at IS NOT NULL) "
            "AND (receipt_json IS NOT NULL OR content_redacted_at IS NOT NULL))",
            name="ck_aqub_v2_delivery_consent_cancellation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_ref: Mapped[str | None] = mapped_column(String(255))
    cube_ref: Mapped[str | None] = mapped_column(String(255))
    route_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    command_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    command_canonical: Mapped[bytes | None] = mapped_column(LargeBinary)
    command_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    ack_ref: Mapped[str | None] = mapped_column(String(255))
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lifecycle_policy_version: Mapped[str | None] = mapped_column(Text)
    lifecycle_consent_ref: Mapped[str | None] = mapped_column(Text)
    lifecycle_receipt_ref: Mapped[str | None] = mapped_column(Text)
    content_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_cancellation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    consent_cancellation_ref: Mapped[str | None] = mapped_column(Text)
    revoked_consent_ref: Mapped[str | None] = mapped_column(Text)
    revoked_authority_epoch: Mapped[int | None] = mapped_column(BigInteger)
    consent_cancellation_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    consent_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AQUBV2LifecyclePolicy(Base):
    __tablename__ = "aqub_v2_lifecycle_policy"

    policy_version: Mapped[str] = mapped_column(Text, primary_key=True)
    policy_digest: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    policy_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    retention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tombstone_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AQUBV2LifecycleReceipt(Base):
    __tablename__ = "aqub_v2_lifecycle_receipt"

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    receipt_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    record_digest: Mapped[str] = mapped_column(Text, nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    policy_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey("aqub_v2_lifecycle_policy.policy_version"),
        nullable=False,
    )
    consent_ref: Mapped[str] = mapped_column(Text, nullable=False)
    prior_state: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AQUBV2RouteAuthority(Base):
    __tablename__ = "aqub_v2_route_authority"
    __table_args__ = (
        CheckConstraint("state IN ('active','hold')", name="ck_aqub_v2_route_state"),
        CheckConstraint("route_generation > 0", name="ck_aqub_v2_route_generation"),
        CheckConstraint("authority_epoch > 0", name="ck_aqub_v2_route_epoch"),
        CheckConstraint(
            "installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_route_installation_ref",
        ),
        CheckConstraint(
            "conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_route_conversation_ref",
        ),
        CheckConstraint(
            "cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$'",
            name="ck_aqub_v2_route_cube_ref",
        ),
    )

    installation_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    cube_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    route_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    authority_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
