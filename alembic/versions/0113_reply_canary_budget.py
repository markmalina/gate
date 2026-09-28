"""Durable exact-scope canary attempt ledger; no activation or sends."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0113_reply_canary_budget"
down_revision = "0112_reply_sequence_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_canary_scopes",
        sa.Column("workspace_ref", sa.String(length=128), primary_key=True),
        sa.Column("execution_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("sequence_grants", JSONB(), nullable=False),
        sa.Column("notification_destination", sa.String(length=255), nullable=False),
        sa.Column("notification_template", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempts_reserved", sa.Integer(), nullable=False),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("state IN ('active','closed')"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 6"),
        sa.CheckConstraint("attempts_reserved >= 0 AND attempts_reserved <= max_attempts"),
    )
    op.create_table(
        "reply_canary_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_ref", sa.String(length=128), nullable=False),
        sa.Column("attempt_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("execution_id", sa.String(length=128), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_ref"], ["reply_canary_scopes.workspace_ref"]),
        sa.UniqueConstraint("workspace_ref", "attempt_key"),
    )
    op.execute("""
        CREATE FUNCTION guard_reply_canary_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'canary scope cannot be deleted';
            END IF;
            IF NEW.workspace_ref IS DISTINCT FROM OLD.workspace_ref
                OR NEW.execution_id IS DISTINCT FROM OLD.execution_id
                OR NEW.principal_subject IS DISTINCT FROM OLD.principal_subject
                OR NEW.sequence_grants IS DISTINCT FROM OLD.sequence_grants
                OR NEW.notification_destination IS DISTINCT FROM OLD.notification_destination
                OR NEW.notification_template IS DISTINCT FROM OLD.notification_template
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
                OR NEW.scope_sha256 IS DISTINCT FROM OLD.scope_sha256
                OR NEW.attempts_reserved < OLD.attempts_reserved
                OR NEW.attempts_reserved > OLD.attempts_reserved + 1
                OR (OLD.state = 'closed' AND NEW.state <> 'closed')
                OR (OLD.state = 'closed' AND NEW.attempts_reserved <> OLD.attempts_reserved)
            THEN
                RAISE EXCEPTION 'canary scope is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER guard_reply_canary_scope_update
        BEFORE UPDATE OR DELETE ON reply_canary_scopes
        FOR EACH ROW EXECUTE FUNCTION guard_reply_canary_scope()
    """)


def downgrade() -> None:
    op.drop_table("reply_canary_attempts")
    op.drop_table("reply_canary_scopes")
    op.execute("DROP FUNCTION guard_reply_canary_scope()")
