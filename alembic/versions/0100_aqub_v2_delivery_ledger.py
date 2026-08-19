"""Add the default-off durable AQUB v2 delivery ledger.

Revision ID: 0100_aqub_v2_delivery_ledger
Revises: 0099_telegram_caption_backfill
"""

from alembic import op

revision = "0100_aqub_v2_delivery_ledger"
down_revision = "0099_telegram_caption_backfill"
branch_labels = None
depends_on = None

ROUTE_AUTHORITY_SQL = """
CREATE TABLE aqub_v2_route_authority (
  installation_ref varchar(255) NOT NULL,
  conversation_ref varchar(255) NOT NULL,
  cube_ref varchar(255) NOT NULL,
  route_generation integer NOT NULL
    CONSTRAINT ck_aqub_v2_route_generation CHECK (route_generation > 0),
  state varchar(16) NOT NULL
    CONSTRAINT ck_aqub_v2_route_state CHECK (state IN ('active','hold')),
  authority_epoch bigint NOT NULL
    CONSTRAINT ck_aqub_v2_route_epoch CHECK (authority_epoch > 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(installation_ref,conversation_ref),
  CONSTRAINT ck_aqub_v2_route_installation_ref
    CHECK (installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_route_conversation_ref
    CHECK (conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_route_cube_ref
    CHECK (cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$')
)
"""

DELIVERY_LEDGER_SQL = """
CREATE TABLE aqub_v2_delivery_ledger (
  id uuid PRIMARY KEY,
  command_id uuid NOT NULL UNIQUE,
  installation_ref varchar(255) NOT NULL,
  conversation_ref varchar(255) NOT NULL,
  cube_ref varchar(255) NOT NULL,
  route_generation integer NOT NULL
    CONSTRAINT ck_aqub_v2_delivery_generation CHECK (route_generation > 0),
  idempotency_key varchar(255) NOT NULL,
  command_sha256 varchar(64) NOT NULL
    CONSTRAINT ck_aqub_v2_command_sha CHECK (char_length(command_sha256)=64),
  command_json jsonb NOT NULL,
  state varchar(32) NOT NULL
    CONSTRAINT ck_aqub_v2_delivery_state
    CHECK (state IN ('committed','sending','sent','failed','unknown','rejected')),
  ack_ref varchar(255) NOT NULL,
  send_started_at timestamptz,
  settled_at timestamptz,
  receipt_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_aqub_v2_delivery_key UNIQUE(installation_ref,idempotency_key),
  CONSTRAINT ck_aqub_v2_delivery_installation_ref
    CHECK (installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_delivery_conversation_ref
    CHECK (conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_delivery_cube_ref
    CHECK (cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$')
)
"""

DELIVERY_GUARD_FUNCTION_SQL = """
CREATE FUNCTION enforce_aqub_v2_delivery_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(NEW.id,NEW.command_id,NEW.installation_ref,NEW.conversation_ref,
         NEW.cube_ref,NEW.route_generation,NEW.idempotency_key,
         NEW.command_sha256,NEW.command_json,NEW.ack_ref,NEW.created_at)
     IS DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.conversation_ref,
         OLD.cube_ref,OLD.route_generation,OLD.idempotency_key,
         OLD.command_sha256,OLD.command_json,OLD.ack_ref,OLD.created_at) THEN
    RAISE EXCEPTION 'AQUB v2 delivery identity is immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.send_started_at IS NOT NULL AND
     NEW.send_started_at IS DISTINCT FROM OLD.send_started_at THEN
    RAISE EXCEPTION 'AQUB v2 send boundary is irreversible' USING ERRCODE='23514';
  END IF;
  IF OLD.send_started_at IS NULL AND NEW.send_started_at IS NOT NULL AND
     NOT (OLD.state='committed' AND NEW.state='sending') THEN
    RAISE EXCEPTION 'AQUB v2 send boundary requires committed to sending'
      USING ERRCODE='23514';
  END IF;
  IF NEW.state='sending' AND NEW.send_started_at IS NULL THEN
    RAISE EXCEPTION 'AQUB v2 sending requires boundary evidence' USING ERRCODE='23514';
  END IF;
  IF OLD.state=NEW.state AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'AQUB v2 same-state mutation is forbidden' USING ERRCODE='23514';
  END IF;
  IF NOT (
    (OLD.state='committed' AND NEW.state IN ('sending','rejected')) OR
    (OLD.state='sending' AND NEW.state IN ('sent','failed','unknown')) OR
    (OLD.state='unknown' AND NEW.state IN ('sent','failed'))
  ) THEN
    RAISE EXCEPTION 'invalid AQUB v2 delivery transition % -> %',OLD.state,NEW.state
      USING ERRCODE='23514';
  END IF;
  IF NEW.state IN ('sent','failed','unknown','rejected') AND
     (NEW.receipt_json IS NULL OR NEW.settled_at IS NULL) THEN
    RAISE EXCEPTION 'AQUB v2 terminal transition requires evidence' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$$
"""

DELIVERY_GUARD_TRIGGER_SQL = """
CREATE TRIGGER trg_aqub_v2_delivery_transition
BEFORE UPDATE ON aqub_v2_delivery_ledger
FOR EACH ROW EXECUTE FUNCTION enforce_aqub_v2_delivery_transition()
"""

ROUTE_GUARD_FUNCTION_SQL = """
CREATE FUNCTION enforce_aqub_v2_route_authority_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(NEW.installation_ref,NEW.conversation_ref) IS DISTINCT FROM
     ROW(OLD.installation_ref,OLD.conversation_ref) THEN
    RAISE EXCEPTION 'AQUB v2 route identity is immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.authority_epoch <= OLD.authority_epoch THEN
    RAISE EXCEPTION 'AQUB v2 route authority epoch must increase' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$$
"""

ROUTE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER trg_aqub_v2_route_authority_transition
BEFORE UPDATE ON aqub_v2_route_authority
FOR EACH ROW EXECUTE FUNCTION enforce_aqub_v2_route_authority_transition()
"""

LEDGER_GUARD_STATEMENTS = (
    DELIVERY_GUARD_FUNCTION_SQL,
    DELIVERY_GUARD_TRIGGER_SQL,
    ROUTE_GUARD_FUNCTION_SQL,
    ROUTE_GUARD_TRIGGER_SQL,
)


def upgrade() -> None:
    op.execute(ROUTE_AUTHORITY_SQL)
    op.execute(DELIVERY_LEDGER_SQL)
    for statement in LEDGER_GUARD_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION enforce_aqub_v2_route_authority_transition() CASCADE")
    op.execute("DROP FUNCTION enforce_aqub_v2_delivery_transition() CASCADE")
    op.drop_table("aqub_v2_delivery_ledger")
    op.drop_table("aqub_v2_route_authority")
