"""Bind Work text-part receipts to immutable GitHub comment identities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0119_watch_voice_comment_ids"
down_revision: str | None = "0118_watch_voice_work_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing unidentified receipts stay unidentified; no inferred backfill.
    with op.batch_alter_table("watch_voice_text_receipts") as batch:
        batch.add_column(sa.Column("comment_id", sa.BigInteger(), nullable=True))
        batch.create_check_constraint("ck_watch_voice_text_receipt_comment_id",
                                      "comment_id IS NULL OR comment_id > 0")
        batch.create_unique_constraint("uq_watch_voice_text_receipt_comment",
                                       ["note_id", "comment_id"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT count(*) FROM watch_voice_text_receipts WHERE comment_id IS NOT NULL"
    )).scalar_one():
        raise RuntimeError("watch_voice_comment_downgrade_would_lose_identity")
    with op.batch_alter_table("watch_voice_text_receipts") as batch:
        batch.drop_constraint("uq_watch_voice_text_receipt_comment", type_="unique")
        batch.drop_constraint("ck_watch_voice_text_receipt_comment_id", type_="check")
        batch.drop_column("comment_id")
