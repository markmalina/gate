"""Create immutable BECOME I4 recovery v2 attempt and append-only events.

Revision ID: 0081_become_i4_recovery_v2
Revises: 0080_become_i4_recovery
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0081_become_i4_recovery_v2"
down_revision: str | None = "0080_become_i4_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTEMPT_SCHEMA = "gate.become-i4-recovery-attempt@2"
CONTRACT_DIGEST = "91c3a210709c63738358bd0c512d11a9668acabd0997afcd68ba240bed38f573"
MAYAKI_SOURCE_SHA = "2078d230852af1c7e3a309f00d716fa2fc664f68"
OKO_SOURCE_SHA = "2c304529d04be43726e677d2b864c4b5f8283be8"
TAKE_SOURCE_SHA = "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"


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
        "become_i4_recovery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "recovery_receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("attempt_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("max_posts", sa.Integer(), nullable=False),
        sa.Column("prepared_recovery_digest", sa.String(length=64), nullable=False),
        sa.Column("original_request_digest", sa.String(length=64), nullable=False),
        sa.Column("original_receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("relations_receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("transcript_digest", sa.String(length=64), nullable=False),
        sa.Column("contract_digest", sa.String(length=64), nullable=False),
        sa.Column("mayaki_source_sha", sa.String(length=40), nullable=False),
        sa.Column("oko_source_sha", sa.String(length=40), nullable=False),
        sa.Column("take_source_sha", sa.String(length=40), nullable=False),
        sa.Column("attempt_digest", sa.String(length=64), nullable=False),
        sa.Column("attempt_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-i4-recovery-attempt@2' "
            "AND attempt_version = 2 AND state = 'prepared'",
            name="ck_become_i4_recovery_attempt_identity",
        ),
        sa.CheckConstraint(
            "max_posts = 3",
            name="ck_become_i4_recovery_attempt_max_posts",
        ),
        sa.CheckConstraint(
            "char_length(prepared_recovery_digest) = 64 "
            "AND char_length(original_request_digest) = 64 "
            "AND char_length(original_receipt_digest) = 64 "
            "AND char_length(relations_receipt_digest) = 64 "
            "AND char_length(transcript_digest) = 64 "
            "AND char_length(contract_digest) = 64 "
            "AND char_length(mayaki_source_sha) = 40 "
            "AND char_length(oko_source_sha) = 40 "
            "AND char_length(take_source_sha) = 40 "
            "AND char_length(attempt_digest) = 64",
            name="ck_become_i4_recovery_attempt_digests",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_i4_recovery_attempt_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["become_voice_correlations.id"],
            name="fk_become_i4_recovery_attempt_correlation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recovery_receipt_id"],
            ["become_i4_recovery_receipts.id"],
            name="fk_become_i4_recovery_attempt_receipt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recovery_receipt_id",
            name="uq_become_i4_recovery_attempt_receipt",
        ),
        sa.UniqueConstraint(
            "correlation_id",
            "attempt_version",
            name="uq_become_i4_recovery_attempt_version",
        ),
    )
    op.create_index(
        "ix_become_i4_recovery_attempt_workspace",
        "become_i4_recovery_attempts",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "become_i4_recovery_stage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("receipt_payload", postgresql.JSONB(), nullable=True),
        sa.Column("source_sha", sa.String(length=40), nullable=False),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("event_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('mayaki', 'oko', 'take') "
            "AND ordinal IN (2, 3, 4)",
            name="ck_become_i4_recovery_stage_identity",
        ),
        sa.CheckConstraint(
            "(event_sequence = 1 AND event_type = 'reserved') "
            "OR (event_sequence = 2 "
            "AND event_type IN ('completed', 'failed', 'unknown'))",
            name="ck_become_i4_recovery_event_order",
        ),
        sa.CheckConstraint(
            "char_length(request_digest) = 64 "
            "AND char_length(source_sha) = 40 "
            "AND char_length(event_digest) = 64 "
            "AND (receipt_digest IS NULL "
            "OR char_length(receipt_digest) = 64)",
            name="ck_become_i4_recovery_event_digests",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_i4_recovery_event_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["become_i4_recovery_attempts.id"],
            name="fk_become_i4_recovery_event_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            "stage",
            "event_sequence",
            name="uq_become_i4_recovery_stage_sequence",
        ),
    )
    op.create_index(
        "ix_become_i4_recovery_event_attempt",
        "become_i4_recovery_stage_events",
        ["attempt_id", "ordinal", "event_sequence"],
        unique=False,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                recovery.id AS recovery_receipt_id,
                recovery.workspace_id,
                recovery.correlation_id,
                recovery.receipt_digest AS prepared_recovery_digest,
                recovery.original_request_digest,
                recovery.original_receipt_digest,
                correlation.correlation_ref,
                intake.transcript_digest,
                relations.receipt_digest AS relations_receipt_digest
            FROM become_i4_recovery_receipts AS recovery
            JOIN become_voice_correlations AS correlation
              ON correlation.id = recovery.correlation_id
            JOIN become_media_intakes AS intake
              ON intake.id = correlation.media_intake_id
            JOIN become_voice_chain_slots AS relations
              ON relations.correlation_id = recovery.correlation_id
             AND relations.stage = 'relations'
             AND relations.state = 'completed'
            WHERE recovery.state = 'prepared'
              AND recovery.enabled = false
              AND recovery.attempt_version = 2
            """
        )
    ).mappings()
    attempt_table = sa.table(
        "become_i4_recovery_attempts",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("correlation_id", postgresql.UUID(as_uuid=True)),
        sa.column("recovery_receipt_id", postgresql.UUID(as_uuid=True)),
        sa.column("schema_name", sa.String()),
        sa.column("attempt_version", sa.Integer()),
        sa.column("state", sa.String()),
        sa.column("max_posts", sa.Integer()),
        sa.column("prepared_recovery_digest", sa.String()),
        sa.column("original_request_digest", sa.String()),
        sa.column("original_receipt_digest", sa.String()),
        sa.column("relations_receipt_digest", sa.String()),
        sa.column("transcript_digest", sa.String()),
        sa.column("contract_digest", sa.String()),
        sa.column("mayaki_source_sha", sa.String()),
        sa.column("oko_source_sha", sa.String()),
        sa.column("take_source_sha", sa.String()),
        sa.column("attempt_digest", sa.String()),
        sa.column("attempt_payload", postgresql.JSONB()),
    )
    for row in rows:
        for field in (
            "prepared_recovery_digest",
            "original_request_digest",
            "original_receipt_digest",
            "transcript_digest",
            "relations_receipt_digest",
        ):
            value = row[field]
            if not isinstance(value, str) or len(value) != 64:
                raise RuntimeError("become_i4_recovery_attempt_evidence_invalid")
        attempt_seed = {
            "prepared_recovery_digest": row["prepared_recovery_digest"],
            "correlation_ref": row["correlation_ref"],
            "attempt_version": 2,
        }
        attempt_ref = (
            "synthetic://recovery/attempt/"
            + _digest(attempt_seed)[:32]
        )
        payload = {
            "schema": ATTEMPT_SCHEMA,
            "attempt_ref": attempt_ref,
            "attempt_version": 2,
            "correlation_ref": row["correlation_ref"],
            "prepared_recovery_digest": row["prepared_recovery_digest"],
            "original_failed_request_digest": row["original_request_digest"],
            "original_failed_receipt_digest": row["original_receipt_digest"],
            "relations_receipt_digest": row["relations_receipt_digest"],
            "transcript_digest": row["transcript_digest"],
            "contract_digest": CONTRACT_DIGEST,
            "source_shas": {
                "mayaki": MAYAKI_SOURCE_SHA,
                "oko": OKO_SOURCE_SHA,
                "take": TAKE_SOURCE_SHA,
            },
            "stage_order": ["mayaki", "oko", "take"],
            "max_posts": 3,
            "state": "prepared",
            "external_effects": 0,
        }
        bind.execute(
            attempt_table.insert().values(
                id=uuid.uuid4(),
                workspace_id=row["workspace_id"],
                correlation_id=row["correlation_id"],
                recovery_receipt_id=row["recovery_receipt_id"],
                schema_name=ATTEMPT_SCHEMA,
                attempt_version=2,
                state="prepared",
                max_posts=3,
                prepared_recovery_digest=row["prepared_recovery_digest"],
                original_request_digest=row["original_request_digest"],
                original_receipt_digest=row["original_receipt_digest"],
                relations_receipt_digest=row["relations_receipt_digest"],
                transcript_digest=row["transcript_digest"],
                contract_digest=CONTRACT_DIGEST,
                mayaki_source_sha=MAYAKI_SOURCE_SHA,
                oko_source_sha=OKO_SOURCE_SHA,
                take_source_sha=TAKE_SOURCE_SHA,
                attempt_digest=_digest(payload),
                attempt_payload=payload,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_become_i4_recovery_event_attempt",
        table_name="become_i4_recovery_stage_events",
    )
    op.drop_table("become_i4_recovery_stage_events")
    op.drop_index(
        "ix_become_i4_recovery_attempt_workspace",
        table_name="become_i4_recovery_attempts",
    )
    op.drop_table("become_i4_recovery_attempts")
