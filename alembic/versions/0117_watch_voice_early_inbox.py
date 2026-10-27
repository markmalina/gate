"""Add an independent early-card journal; preserve all legacy sources/originals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0117_watch_voice_early_inbox"
down_revision: str | None = "0116_watch_voice_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("watch_voice_sources") as batch:
        batch.add_column(sa.Column("card_state", sa.String(16), nullable=False,
                                   server_default="legacy"))
        batch.add_column(sa.Column("card_body", sa.Text(), nullable=True))
        batch.add_column(sa.Column("card_body_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("card_title", sa.String(256), nullable=True))
        batch.add_column(sa.Column("card_claim_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("card_writer", sa.String(128), nullable=True))
        batch.add_column(sa.Column("card_attempted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("card_issue_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("card_issue_url", sa.String(512), nullable=True))
        batch.add_column(sa.Column("card_received_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("card_error", sa.String(128), nullable=True))
        batch.create_check_constraint(
            "ck_watch_voice_source_card_state",
            "card_state IN ('legacy','pending','sending','unknown','saved','blocked')",
        )
    op.create_index("ix_watch_voice_source_card", "watch_voice_sources",
                    ["workspace_key", "account_id", "card_state"])
    op.add_column("watch_voice_notes", sa.Column("delivery_writer", sa.String(128), nullable=True))


def downgrade() -> None:
    # No automatic Production downgrade is authorized by this migration source.
    active = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM watch_voice_sources WHERE card_state <> 'legacy'"
    )).scalar_one()
    if active:
        raise RuntimeError("watch_voice_card_downgrade_would_lose_receipts")
    active_writers = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM watch_voice_notes WHERE delivery_writer IS NOT NULL"
    )).scalar_one()
    if active_writers:
        raise RuntimeError("watch_voice_card_downgrade_would_lose_receipts")
    with op.batch_alter_table("watch_voice_notes") as batch:
        batch.drop_column("delivery_writer")
    op.drop_index("ix_watch_voice_source_card", table_name="watch_voice_sources")
    with op.batch_alter_table("watch_voice_sources") as batch:
        batch.drop_constraint("ck_watch_voice_source_card_state", type_="check")
        for name in ("card_error", "card_received_at", "card_issue_url", "card_issue_number",
                     "card_attempted_at", "card_writer", "card_claim_id", "card_title",
                     "card_body_sha256", "card_body", "card_state"):
            batch.drop_column(name)
