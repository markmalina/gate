"""Add the shared draft fence for default-off personal Telegram sends."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0109_personal_delivery_fence"
down_revision: str | None = "0108_short_voice_headroom"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_telegram_send_fences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "reply_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_drafts.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("actor_ref", sa.String(128), nullable=False),
        sa.Column("session_ref", sa.String(128), nullable=False),
        sa.Column("peer_ref", sa.String(128), nullable=False),
        sa.Column("text_digest", sa.String(64), nullable=False),
        sa.Column("approval_digest", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("provider_receipt_ref", sa.String(128), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("reply_draft_id", name="uq_personal_send_fences_draft"),
        sa.UniqueConstraint("idempotency_key", name="uq_personal_send_fences_key"),
    )
    op.create_index(
        "ix_personal_send_fences_conversation_status",
        "personal_telegram_send_fences",
        ["conversation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_send_fences_conversation_status",
        table_name="personal_telegram_send_fences",
    )
    op.drop_table("personal_telegram_send_fences")
