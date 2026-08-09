"""Add exact-Space full-history lexical search documents.

Revision ID: 0096_beta_space_search
Revises: 0097_beta_exact_scope
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0096_beta_space_search"
down_revision: str | Sequence[str] | None = "0097_beta_exact_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_space_search_documents",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("sender_label", sa.String(128), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_space_search_event_scope",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "source_kind IN ('text','voice','document','photo')",
            name="ck_beta_space_search_source_kind",
        ),
        sa.CheckConstraint(
            "char_length(content_text) > 0",
            name="ck_beta_space_search_content_nonempty",
        ),
    )
    op.create_index(
        "ix_beta_space_search_scope_occurred",
        "beta_space_search_documents",
        ["workspace_id", "binding_id", "occurred_at", "event_id"],
    )
    op.execute(
        "CREATE INDEX ix_beta_space_search_lexical "
        "ON beta_space_search_documents USING gin "
        "(to_tsvector('simple'::regconfig, content_text))"
    )

    op.execute(
        """
        INSERT INTO beta_space_search_documents (
            event_id,
            workspace_id,
            binding_id,
            source_kind,
            sender_label,
            content_text,
            occurred_at,
            created_at,
            updated_at
        )
        SELECT
            event.id,
            event.workspace_id,
            event.binding_id,
            'text',
            left(
                coalesce(
                    nullif(
                        btrim(
                            concat_ws(
                                ' ',
                                receipt.payload #>> '{message,from,first_name}',
                                receipt.payload #>> '{message,from,last_name}'
                            )
                        ),
                        ''
                    ),
                    'Участник'
                ),
                128
            ),
            receipt.payload #>> '{message,text}',
            event.occurred_at,
            event.created_at,
            event.created_at
        FROM beta_space_events AS event
        JOIN telegram_receipts AS receipt ON receipt.id = event.receipt_id
        WHERE event.event_kind = 'fact'
          AND event.media_kind = 'text'
          AND receipt.payload #>> '{message,chat,id}' = event.telegram_chat_id
          AND receipt.payload #>> '{message,from,is_bot}' = 'false'
          AND nullif(btrim(receipt.payload #>> '{message,text}'), '') IS NOT NULL
        ON CONFLICT (event_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO beta_space_search_documents (
            event_id,
            workspace_id,
            binding_id,
            source_kind,
            sender_label,
            content_text,
            occurred_at,
            created_at,
            updated_at
        )
        SELECT
            job.event_id,
            job.workspace_id,
            job.binding_id,
            job.media_kind,
            job.sender_label,
            CASE
                WHEN nullif(btrim(job.structured_text), '')
                     = nullif(btrim(job.extracted_text), '')
                THEN nullif(btrim(job.structured_text), '')
                ELSE concat_ws(
                    E'\n\n',
                    nullif(btrim(job.structured_text), ''),
                    nullif(btrim(job.extracted_text), '')
                )
            END,
            job.occurred_at,
            job.created_at,
            coalesce(job.processed_at, job.updated_at)
        FROM beta_material_jobs AS job
        WHERE job.state = 'structured'
          AND (
              nullif(btrim(job.structured_text), '') IS NOT NULL
              OR nullif(btrim(job.extracted_text), '') IS NOT NULL
          )
        ON CONFLICT (event_id) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            binding_id = EXCLUDED.binding_id,
            source_kind = EXCLUDED.source_kind,
            sender_label = EXCLUDED.sender_label,
            content_text = EXCLUDED.content_text,
            occurred_at = EXCLUDED.occurred_at,
            updated_at = EXCLUDED.updated_at
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_beta_space_search_lexical",
        table_name="beta_space_search_documents",
    )
    op.drop_index(
        "ix_beta_space_search_scope_occurred",
        table_name="beta_space_search_documents",
    )
    op.drop_table("beta_space_search_documents")
