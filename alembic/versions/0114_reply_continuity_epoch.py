"""Persist verified fleet epochs, process evidence and renewal history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0114_reply_continuity_epoch"
down_revision = "0113_reply_canary_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_continuity_epochs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_ref", sa.String(128), nullable=False, index=True),
        sa.Column("evidence_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("binding_sha256", sa.String(64), nullable=False),
        sa.Column("bot_resource_id", sa.String(64), nullable=False),
        sa.Column("members", JSONB(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("reply_connector_admission", sa.Column("epoch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_reply_connector_epoch",
        "reply_connector_admission",
        "reply_continuity_epochs",
        ["epoch_id"],
        ["id"],
    )
    op.create_table(
        "reply_ingress_processes",
        sa.Column("workspace_ref", sa.String(128), primary_key=True),
        sa.Column("boot_id", sa.String(128), primary_key=True),
        sa.Column("source_sha", sa.String(64), nullable=False),
        sa.Column("bot_resource_id", sa.String(64), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_gap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workers", JSONB(), nullable=False),
    )
    op.create_table(
        "reply_ingress_projections",
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("telegram_receipts.id"), primary_key=True),
        sa.Column("workspace_ref", sa.String(128), nullable=False, index=True),
        sa.Column("boot_id", sa.String(128), nullable=False),
    )
    op.create_table(
        "reply_continuity_renewals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "epoch_id", sa.Uuid(), sa.ForeignKey("reply_continuity_epochs.id"), nullable=False
        ),
        sa.Column("connector_version", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("continuity_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("epoch_id", "connector_version"),
    )
    op.execute("""
        CREATE FUNCTION guard_reply_continuity_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'continuity evidence is immutable';
        END $$
    """)
    for table in ("reply_continuity_epochs", "reply_continuity_renewals"):
        op.execute(f"""CREATE TRIGGER guard_{table} BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION guard_reply_continuity_evidence()""")


def downgrade() -> None:
    op.drop_table("reply_continuity_renewals")
    op.drop_table("reply_ingress_projections")
    op.drop_table("reply_ingress_processes")
    op.drop_constraint("fk_reply_connector_epoch", "reply_connector_admission", type_="foreignkey")
    op.drop_column("reply_connector_admission", "epoch_id")
    op.drop_table("reply_continuity_epochs")
    op.execute("DROP FUNCTION guard_reply_continuity_evidence()")
