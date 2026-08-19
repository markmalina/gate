"""Durable opaque-only ledger for the disabled AQUB delivery v2 seam."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    LargeBinary,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AQUBV2DeliveryLedger(Base):
    __tablename__ = "aqub_v2_delivery_ledger"
    __table_args__ = (
        UniqueConstraint("installation_ref", "idempotency_key", name="uq_aqub_v2_delivery_key"),
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
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    cube_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    route_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    command_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    command_canonical: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    command_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    ack_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
