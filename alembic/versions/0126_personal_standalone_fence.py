"""Allow the shared Telegram fence to reserve a standalone physical peer."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0126_personal_standalone_fence"
down_revision: str | None = "0125_personal_runtime_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "personal_telegram_send_fences",
        sa.Column("physical_peer_key", sa.String(length=64), nullable=True),
    )
    # All pre-0126 rows are draft-bound Business private dialogs. Preserve
    # their physical destination without inventing a standalone command.
    op.execute(
        """
        UPDATE personal_telegram_send_fences AS fence
        SET physical_peer_key = 'user:' || conversation.chat_id
        FROM conversations AS conversation
        WHERE conversation.id = fence.conversation_id
          AND fence.physical_peer_key IS NULL
        """
    )
    op.alter_column(
        "personal_telegram_send_fences",
        "physical_peer_key",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "personal_telegram_send_fences",
        "reply_draft_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "personal_telegram_send_fences",
        "conversation_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_personal_send_fence_optional_draft",
        "personal_telegram_send_fences",
        "(reply_draft_id IS NULL) = (conversation_id IS NULL)",
    )
    op.create_index(
        "ix_personal_send_fences_physical_peer_status",
        "personal_telegram_send_fences",
        ["account_id", "physical_peer_key", "status"],
    )


def downgrade() -> None:
    # A standalone UNKNOWN is delivery evidence. Refuse to erase it merely to
    # satisfy a schema rollback; an operator must reconcile it first.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM personal_telegram_send_fences
                WHERE reply_draft_id IS NULL OR conversation_id IS NULL
            ) THEN
                RAISE EXCEPTION 'standalone personal Telegram fences require reconciliation';
            END IF;
        END
        $$
        """
    )
    op.drop_index(
        "ix_personal_send_fences_physical_peer_status",
        table_name="personal_telegram_send_fences",
    )
    op.drop_constraint(
        "ck_personal_send_fence_optional_draft",
        "personal_telegram_send_fences",
        type_="check",
    )
    op.alter_column(
        "personal_telegram_send_fences",
        "conversation_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "personal_telegram_send_fences",
        "reply_draft_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("personal_telegram_send_fences", "physical_peer_key")
