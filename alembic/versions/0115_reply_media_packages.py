"""Immutable image packages and durable bounded media execution budgets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0115_reply_media_packages"
down_revision: str | None = "0114_watch_voice_recovery_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "reply_media_execution_budgets",
        _id(),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("principal_subject", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("write_cap", sa.Integer(), nullable=False),
        sa.Column("message_cap", sa.Integer(), nullable=False),
        sa.Column("writes_reserved", sa.Integer(), nullable=False),
        sa.Column("messages_reserved", sa.Integer(), nullable=False),
        _created(),
        sa.UniqueConstraint(
            "workspace_id",
            "principal_subject",
            "execution_id",
            name="uq_reply_media_execution_scope",
        ),
        sa.CheckConstraint(
            "write_cap = 12 AND message_cap = 20", name="ck_reply_media_budget_caps"
        ),
        sa.CheckConstraint(
            "writes_reserved >= 0 AND writes_reserved <= write_cap AND "
            "messages_reserved >= 0 AND messages_reserved <= message_cap",
            name="ck_reply_media_budget_counters",
        ),
    )
    op.create_table(
        "reply_media_packages",
        _id(),
        sa.Column(
            "reply_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("principal_subject", sa.String(255), nullable=False),
        sa.Column("staging_key", sa.String(128), nullable=False),
        sa.Column("execution_id", sa.String(128), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("package_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("expected_message_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_json", postgresql.JSONB(), nullable=False),
        _created(),
        sa.UniqueConstraint("reply_draft_id", name="uq_reply_media_package_draft"),
        sa.UniqueConstraint(
            "workspace_id",
            "principal_subject",
            "staging_key",
            name="uq_reply_media_package_staging",
        ),
        sa.CheckConstraint(
            "mode IN ('photo','album','rich_photo','rich_slideshow','rich_collage')",
            name="ck_reply_media_package_mode",
        ),
        sa.CheckConstraint("expected_message_count BETWEEN 1 AND 10", name="ck_reply_media_count"),
    )
    op.create_index(
        "ix_reply_media_packages_reply_draft_id", "reply_media_packages", ["reply_draft_id"]
    )
    op.create_index("ix_reply_media_package_expiry", "reply_media_packages", ["expires_at"])
    op.create_table(
        "reply_media_items",
        _id(),
        sa.Column(
            "package_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_media_packages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("transform_version", sa.String(64), nullable=False),
        sa.Column("transform", sa.String(255), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("caption_format", sa.String(16), nullable=False),
        sa.Column("caption_formatting_json", postgresql.JSONB(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("receipt_json", postgresql.JSONB(), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("package_id", "ordinal", name="uq_reply_media_item_order"),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 10", name="ck_reply_media_item_ordinal"),
        sa.CheckConstraint(
            "size_bytes > 0 AND source_size_bytes > 0", name="ck_reply_media_item_size"
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_reply_media_item_dimensions"),
    )
    op.create_index("ix_reply_media_items_package_id", "reply_media_items", ["package_id"])
    op.create_table(
        "reply_media_budget_reservations",
        _id(),
        sa.Column(
            "send_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("send_attempts.id"),
            nullable=False,
        ),
        sa.Column(
            "budget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reply_media_execution_budgets.id"),
            nullable=False,
        ),
        sa.Column("expected_messages", sa.Integer(), nullable=False),
        _created(),
        sa.UniqueConstraint("send_attempt_id", name="uq_reply_media_budget_attempt"),
        sa.CheckConstraint(
            "expected_messages BETWEEN 1 AND 10", name="ck_reply_media_budget_messages"
        ),
    )
    op.create_index(
        "ix_reply_media_budget_reservations_send_attempt_id",
        "reply_media_budget_reservations",
        ["send_attempt_id"],
    )
    op.create_index(
        "ix_reply_media_budget_reservations_budget_id",
        "reply_media_budget_reservations",
        ["budget_id"],
    )


def downgrade() -> None:
    op.drop_table("reply_media_budget_reservations")
    op.drop_table("reply_media_items")
    op.drop_table("reply_media_packages")
    op.drop_table("reply_media_execution_budgets")
