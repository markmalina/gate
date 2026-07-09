"""Add durable Telegram update and message storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_durable_message_storage"
down_revision: str | None = "0001_initial_gate_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gate_events", sa.Column("telegram_update_id", sa.BigInteger(), nullable=True))
    op.add_column("gate_events", sa.Column("telegram_message_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "gate_events",
        sa.Column("business_connection_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("ix_gate_events_telegram_update_id"),
        "gate_events",
        ["telegram_update_id"],
        unique=False,
    )

    op.create_table(
        "telegram_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.UniqueConstraint("bot_id", "update_id", name="uq_telegram_updates_bot_update_id"),
    )
    op.create_index(op.f("ix_telegram_updates_bot_id"), "telegram_updates", ["bot_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_id", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("business_connection_id", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.ForeignKeyConstraint(["gate_event_id"], ["gate_events.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index(op.f("ix_messages_bot_id"), "messages", ["bot_id"])
    op.create_index(op.f("ix_messages_gate_event_id"), "messages", ["gate_event_id"])
    op.create_index(op.f("ix_messages_telegram_update_id"), "messages", ["telegram_update_id"])
    op.create_index(
        "ix_messages_bot_chat_created_at",
        "messages",
        ["bot_id", "chat_id", "created_at"],
    )
    op.create_index(
        "ix_messages_direction_created_at",
        "messages",
        ["direction", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_direction_created_at", table_name="messages")
    op.drop_index("ix_messages_bot_chat_created_at", table_name="messages")
    op.drop_index(op.f("ix_messages_telegram_update_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_gate_event_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_bot_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_telegram_updates_bot_id"), table_name="telegram_updates")
    op.drop_table("telegram_updates")

    op.drop_index(op.f("ix_gate_events_telegram_update_id"), table_name="gate_events")
    op.drop_column("gate_events", "business_connection_id")
    op.drop_column("gate_events", "telegram_message_id")
    op.drop_column("gate_events", "telegram_update_id")
