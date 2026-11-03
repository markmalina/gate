"""Add Work claim/result/part journals without admitting existing sources."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0118_watch_voice_work_results"
down_revision: str | None = "0117_watch_voice_early_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("watch_voice_notes") as batch:
        batch.add_column(sa.Column("transcript_origin", sa.String(16), nullable=False,
                                   server_default="gate"))
        batch.create_check_constraint("ck_watch_voice_transcript_origin",
                                      "transcript_origin IN ('gate','work')")
    op.create_table(
        "watch_voice_work_results",
        sa.Column("note_id", sa.String(36), sa.ForeignKey("watch_voice_notes.id"),
                  primary_key=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_id", sa.String(36)),
        sa.Column("revision", sa.String(255), nullable=False),
        sa.Column("audio_sha256", sa.String(64), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_url", sa.String(512), nullable=False),
        sa.Column("transcript", sa.Text()),
        sa.Column("transcript_sha256", sa.String(64)),
        sa.Column("characters", sa.Integer()),
        sa.Column("utf8_bytes", sa.Integer()),
        sa.Column("prepared_at", sa.DateTime(timezone=True)),
        sa.Column("saved_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("readback_attempted_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(128)),
        sa.CheckConstraint(
            "state IN ('idle','claimed','started','unknown','prepared','saved','accepted')",
            name="ck_watch_voice_work_result_state"),
        sa.CheckConstraint("epoch >= 0", name="ck_watch_voice_work_result_epoch"),
    )
    op.create_table(
        "watch_voice_work_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("watch_voice_notes.id"), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("actor_sha256", sa.String(64), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("note_id", "epoch", name="uq_watch_voice_work_claim_epoch"),
        sa.UniqueConstraint("note_id", "token_sha256", name="uq_watch_voice_work_claim_token"),
        sa.CheckConstraint("state IN ('claimed','started','expired','unknown','submitted')",
                           name="ck_watch_voice_work_claim_state"),
        sa.CheckConstraint("epoch > 0", name="ck_watch_voice_work_claim_epoch"),
    )
    op.create_table(
        "watch_voice_text_receipts",
        sa.Column("note_id", sa.String(36), sa.ForeignKey("watch_voice_notes.id"),
                  primary_key=True),
        sa.Column("part", sa.Integer(), primary_key=True),
        sa.Column("writer", sa.String(128), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_url", sa.String(512), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("part >= 0", name="ck_watch_voice_text_receipt_part"),
    )


def downgrade() -> None:
    for table in ("watch_voice_work_results", "watch_voice_work_claims",
                  "watch_voice_text_receipts"):
        if op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("watch_voice_work_downgrade_would_lose_journal")
    if op.get_bind().execute(sa.text(
        "SELECT count(*) FROM watch_voice_notes WHERE transcript_origin <> 'gate'"
    )).scalar_one():
        raise RuntimeError("watch_voice_work_downgrade_would_lose_provenance")
    for table in ("watch_voice_text_receipts", "watch_voice_work_claims",
                  "watch_voice_work_results"):
        op.drop_table(table)
    with op.batch_alter_table("watch_voice_notes") as batch:
        batch.drop_constraint("ck_watch_voice_transcript_origin", type_="check")
        batch.drop_column("transcript_origin")
