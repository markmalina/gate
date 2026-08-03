"""Prepare an immutable default-off MAYAKI recovery receipt.

Revision ID: 0080_become_i4_recovery
Revises: 0079_become_voice_transcription
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080_become_i4_recovery"
down_revision: str | None = "0079_become_voice_transcription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECOVERY_SCHEMA = "gate.become-i4-recovery-receipt@1"
TARGET_SOURCE_SHA = "2078d230852af1c7e3a309f00d716fa2fc664f68"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    op.create_table(
        "become_i4_recovery_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("failed_slot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("attempt_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("original_request_digest", sa.String(length=64), nullable=False),
        sa.Column("original_receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("original_source_sha", sa.String(length=40), nullable=False),
        sa.Column("target_source_sha", sa.String(length=40), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("receipt_payload", postgresql.JSONB(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-i4-recovery-receipt@1'",
            name="ck_become_i4_recovery_schema",
        ),
        sa.CheckConstraint(
            "stage = 'mayaki' AND attempt_version = 2",
            name="ck_become_i4_recovery_stage_version",
        ),
        sa.CheckConstraint(
            "state = 'prepared' AND enabled = false",
            name="ck_become_i4_recovery_default_off",
        ),
        sa.CheckConstraint(
            "char_length(receipt_digest) = 64 "
            "AND char_length(original_request_digest) = 64 "
            "AND char_length(original_receipt_digest) = 64 "
            "AND char_length(original_source_sha) = 40 "
            "AND char_length(target_source_sha) = 40",
            name="ck_become_i4_recovery_digests",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_i4_recovery_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            name="fk_become_i4_recovery_correlation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["failed_slot_id"],
            ["become_voice_chain_slots.id"],
            name="fk_become_i4_recovery_failed_slot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "failed_slot_id",
            name="uq_become_i4_recovery_failed_slot",
        ),
        sa.UniqueConstraint(
            "correlation_id",
            "stage",
            "attempt_version",
            name="uq_become_i4_recovery_attempt",
        ),
    )
    op.create_index(
        "ix_become_i4_recovery_workspace_state",
        "become_i4_recovery_receipts",
        ["workspace_id", "state"],
        unique=False,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                slot.id AS failed_slot_id,
                slot.workspace_id,
                slot.correlation_id,
                slot.request_digest,
                slot.receipt_digest,
                slot.source_sha,
                slot.receipt_payload,
                correlation.correlation_ref
            FROM become_voice_chain_slots AS slot
            JOIN become_voice_correlations AS correlation
              ON correlation.id = slot.correlation_id
            WHERE slot.stage = 'mayaki'
              AND slot.state = 'failed'
            """
        )
    ).mappings()
    recovery_table = sa.table(
        "become_i4_recovery_receipts",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("correlation_id", postgresql.UUID(as_uuid=True)),
        sa.column("failed_slot_id", postgresql.UUID(as_uuid=True)),
        sa.column("schema_name", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("attempt_version", sa.Integer()),
        sa.column("state", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("original_request_digest", sa.String()),
        sa.column("original_receipt_digest", sa.String()),
        sa.column("original_source_sha", sa.String()),
        sa.column("target_source_sha", sa.String()),
        sa.column("receipt_digest", sa.String()),
        sa.column("receipt_payload", postgresql.JSONB()),
        sa.column("prepared_at", sa.DateTime(timezone=True)),
    )
    prepared_at = datetime.now(UTC)
    for row in rows:
        failure = row["receipt_payload"]
        if (
            not isinstance(failure, dict)
            or failure.get("schema") != "gate.become-i4-stage-outcome@1"
            or failure.get("stage") != "mayaki"
            or failure.get("classification") != "failed"
            or failure.get("error_code") != "connection_not_established"
            or failure.get("external_effects") != 0
        ):
            raise RuntimeError("become_i4_source_failure_invalid")
        request_digest = row["request_digest"]
        receipt_digest = row["receipt_digest"]
        source_sha = row["source_sha"]
        if (
            not isinstance(request_digest, str)
            or len(request_digest) != 64
            or not isinstance(receipt_digest, str)
            or len(receipt_digest) != 64
            or not isinstance(source_sha, str)
            or len(source_sha) != 40
        ):
            raise RuntimeError("become_i4_source_failure_digests_invalid")
        payload = {
            "schema": RECOVERY_SCHEMA,
            "stage": "mayaki",
            "attempt_version": 2,
            "correlation_ref": row["correlation_ref"],
            "original_request_digest": request_digest,
            "original_receipt_digest": receipt_digest,
            "original_source_sha": source_sha,
            "target_source_sha": TARGET_SOURCE_SHA,
            "state": "prepared",
            "enabled": False,
            "external_effects": 0,
        }
        bind.execute(
            recovery_table.insert().values(
                id=uuid.uuid4(),
                workspace_id=row["workspace_id"],
                correlation_id=row["correlation_id"],
                failed_slot_id=row["failed_slot_id"],
                schema_name=RECOVERY_SCHEMA,
                stage="mayaki",
                attempt_version=2,
                state="prepared",
                enabled=False,
                original_request_digest=request_digest,
                original_receipt_digest=receipt_digest,
                original_source_sha=source_sha,
                target_source_sha=TARGET_SOURCE_SHA,
                receipt_digest=_digest(payload),
                receipt_payload=payload,
                prepared_at=prepared_at,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_become_i4_recovery_workspace_state",
        table_name="become_i4_recovery_receipts",
    )
    op.drop_table("become_i4_recovery_receipts")
