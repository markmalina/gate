"""Durable Voice observations and source failures; no historical inference."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116_watch_voice_observability"
down_revision: str | None = "0115_watch_voice_post_payment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watch_voice_cycles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_key", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=255), nullable=False),
        sa.Column("driver", sa.String(length=16), nullable=False),
        sa.Column("source_sha", sa.String(length=40), nullable=True),
        sa.Column("owns_cursor", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_error", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "driver IN ('actions','persistent')", name="ck_watch_voice_cycle_driver"
        ),
        sa.CheckConstraint(
            "outcome IN ('running','success','failure','cancelled')",
            name="ck_watch_voice_cycle_outcome",
        ),
    )
    op.create_index(
        "ix_watch_voice_cycle_scope_time",
        "watch_voice_cycles",
        ["workspace_key", "account_id", "started_at"],
    )
    op.create_table(
        "watch_voice_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_key", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("dropbox_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_id", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_watch_voice_source_attempt_count"),
        sa.CheckConstraint(
            "state IN ('pending','downloading','retry','archived','rejected')",
            name="ck_watch_voice_source_state",
        ),
        sa.UniqueConstraint(
            "workspace_key",
            "account_id",
            "file_id",
            "revision",
            name="uq_watch_voice_discovered_revision",
        ),
    )
    op.create_index(
        "ix_watch_voice_source_scope_state",
        "watch_voice_sources",
        ["workspace_key", "account_id", "state"],
    )
    op.create_table(
        "watch_voice_incidents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_key", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("codes_json", sa.Text(), nullable=False),
        sa.Column("attempted_codes_json", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("log_attempt_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_watch_voice_incident_open",
        "watch_voice_incidents",
        ["workspace_key", "account_id"],
        unique=True,
        postgresql_where=sa.text("recovered_at IS NULL"),
        sqlite_where=sa.text("recovered_at IS NULL"),
    )
    op.create_index(
        "ix_watch_voice_incident_scope_time",
        "watch_voice_incidents",
        ["workspace_key", "account_id", "opened_at"],
    )
    op.add_column(
        "watch_voice_notes", sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "watch_voice_notes",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "watch_voice_notes",
        sa.Column("processing_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "watch_voice_notes", sa.Column("processing_attempt_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "watch_voice_notes",
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "watch_voice_notes",
        sa.Column("provider_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "watch_voice_notes", sa.Column("provider_request_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "watch_voice_notes", sa.Column("provider_finished_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "watch_voice_notes", sa.Column("provider_elapsed_ms", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_watch_voice_note_scope_time",
        "watch_voice_notes",
        ["workspace_key", "account_id", "received_at", "id"],
    )


def downgrade() -> None:
    # A real-data rollback must keep these journals; downgrade is for disposable CI only.
    op.drop_index("uq_watch_voice_incident_open", table_name="watch_voice_incidents")
    op.drop_index("ix_watch_voice_incident_scope_time", table_name="watch_voice_incidents")
    op.drop_table("watch_voice_incidents")
    op.drop_index("ix_watch_voice_note_scope_time", table_name="watch_voice_notes")
    op.drop_column("watch_voice_notes", "detected_at")
    op.drop_column("watch_voice_notes", "processing_started_at")
    op.drop_column("watch_voice_notes", "processing_finished_at")
    op.drop_column("watch_voice_notes", "processing_attempt_count")
    op.drop_column("watch_voice_notes", "provider_started_at")
    op.drop_column("watch_voice_notes", "provider_finished_at")
    op.drop_column("watch_voice_notes", "provider_request_count")
    op.drop_column("watch_voice_notes", "provider_finished_count")
    op.drop_column("watch_voice_notes", "provider_elapsed_ms")
    op.drop_index("ix_watch_voice_source_scope_state", table_name="watch_voice_sources")
    op.drop_table("watch_voice_sources")
    op.drop_index("ix_watch_voice_cycle_scope_time", table_name="watch_voice_cycles")
    op.drop_table("watch_voice_cycles")
