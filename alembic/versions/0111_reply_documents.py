"""Store bounded staged Business documents beside reply drafts.

Revision ID: 0111_reply_documents
Revises: 0110_gate_voice_three_dollar_cap
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0111_reply_documents"
down_revision: str | None = "0110_gate_voice_three_dollar_cap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reply_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reply_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_subject", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("caption", sa.String(1024), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("reply_draft_id", name="uq_reply_documents_draft"),
    )
    op.create_index("ix_reply_documents_reply_draft_id", "reply_documents", ["reply_draft_id"])
    op.create_index("ix_reply_documents_expires_at", "reply_documents", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_reply_documents_expires_at", table_name="reply_documents")
    op.drop_index("ix_reply_documents_reply_draft_id", table_name="reply_documents")
    op.drop_table("reply_documents")
