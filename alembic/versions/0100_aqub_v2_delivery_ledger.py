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
  command_canonical bytea NOT NULL,
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
    CHECK (cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_delivery_idempotency_key
    CHECK (idempotency_key ~ '^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$'),
  CONSTRAINT ck_aqub_v2_delivery_ack_ref
    CHECK (ack_ref ~ '^ack:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
  CONSTRAINT ck_aqub_v2_delivery_command_sha_hex
    CHECK (command_sha256 ~ '^[0-9a-f]{64}$')
)
"""

DELIVERY_GUARD_FUNCTION_SQL = """
CREATE FUNCTION enforce_aqub_v2_delivery_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(NEW.id,NEW.command_id,NEW.installation_ref,NEW.conversation_ref,
         NEW.cube_ref,NEW.route_generation,NEW.idempotency_key,
         NEW.command_sha256,NEW.command_canonical,NEW.command_json,
         NEW.ack_ref,NEW.created_at)
     IS DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.conversation_ref,
         OLD.cube_ref,OLD.route_generation,OLD.idempotency_key,
         OLD.command_sha256,OLD.command_canonical,OLD.command_json,
         OLD.ack_ref,OLD.created_at) THEN
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
  IF NEW.state IN ('sent','failed','unknown','rejected') THEN
    PERFORM assert_aqub_v2_delivery_receipt(NEW.command_json,NEW.receipt_json,NEW.state);
  END IF;
  RETURN NEW;
END
$$
"""

DELIVERY_INSERT_GUARD_FUNCTION_SQL = """
CREATE FUNCTION enforce_aqub_v2_delivery_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE command_keys integer;
DECLARE payload_keys integer;
BEGIN
  SELECT count(*) INTO command_keys FROM jsonb_object_keys(NEW.command_json);
  SELECT count(*) INTO payload_keys FROM jsonb_object_keys(NEW.command_json->'payload');
  IF NEW.state <> 'committed' OR NEW.send_started_at IS NOT NULL OR
     NEW.settled_at IS NOT NULL OR NEW.receipt_json IS NOT NULL THEN
    RAISE EXCEPTION 'AQUB v2 insert must be evidence-free committed state'
      USING ERRCODE='23514';
  END IF;
  IF jsonb_typeof(NEW.command_json) IS DISTINCT FROM 'object' OR
     jsonb_typeof(NEW.command_json->'payload') IS DISTINCT FROM 'object' OR
     command_keys <> 9 OR payload_keys <> 4 OR
     jsonb_typeof(NEW.command_json->'schema_version') IS DISTINCT FROM 'string' OR
     NEW.command_json->>'schema_version' IS DISTINCT FROM 'aqub.delivery-command.v2' OR
     NOT (NEW.command_json ?& ARRAY['schema_version','command_id','idempotency_key',
       'installation_ref','cube_ref','route_generation','conversation_ref',
       'requested_at','payload']) OR
     NOT (NEW.command_json->'payload' ?&
       ARRAY['kind','media_type','text','reply_to_message_ref']) OR
     NEW.command_json->>'command_id' IS DISTINCT FROM NEW.command_id::text OR
     NEW.command_json->>'installation_ref' IS DISTINCT FROM NEW.installation_ref OR
     NEW.command_json->>'conversation_ref' IS DISTINCT FROM NEW.conversation_ref OR
     NEW.command_json->>'cube_ref' IS DISTINCT FROM NEW.cube_ref OR
     NEW.command_json->>'route_generation' IS DISTINCT FROM NEW.route_generation::text OR
     jsonb_typeof(NEW.command_json->'route_generation') IS DISTINCT FROM 'number' OR
     NEW.command_json->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key OR
     jsonb_typeof(NEW.command_json->'command_id') IS DISTINCT FROM 'string' OR
     jsonb_typeof(NEW.command_json->'idempotency_key') IS DISTINCT FROM 'string' OR
     jsonb_typeof(NEW.command_json->'installation_ref') IS DISTINCT FROM 'string' OR
     jsonb_typeof(NEW.command_json->'conversation_ref') IS DISTINCT FROM 'string' OR
     jsonb_typeof(NEW.command_json->'cube_ref') IS DISTINCT FROM 'string' OR
     jsonb_typeof(NEW.command_json->'requested_at') IS DISTINCT FROM 'string' OR
     COALESCE(NEW.command_json->>'requested_at' !~
       '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-5][0-9]\\.[0-9]{3}Z$',true) OR
     jsonb_typeof(NEW.command_json->'payload'->'kind') IS DISTINCT FROM 'string' OR
     NEW.command_json->'payload'->>'kind' IS DISTINCT FROM 'text' OR
     jsonb_typeof(NEW.command_json->'payload'->'media_type') IS DISTINCT FROM 'string' OR
     COALESCE(NEW.command_json->'payload'->>'media_type' !~
       '^[a-z][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$',true) OR
     jsonb_typeof(NEW.command_json->'payload'->'text') IS DISTINCT FROM 'string' OR
     COALESCE(length(NEW.command_json->'payload'->>'text') NOT BETWEEN 1 AND 32768,true) OR
     jsonb_typeof(NEW.command_json->'payload'->'reply_to_message_ref')
       NOT IN ('string','null') OR
     (jsonb_typeof(NEW.command_json->'payload'->'reply_to_message_ref')='string' AND
      COALESCE(NEW.command_json->'payload'->>'reply_to_message_ref' !~
        '^message:[A-Za-z][A-Za-z0-9._~-]{7,127}$',true)) OR
     convert_from(NEW.command_canonical,'UTF8')::jsonb IS DISTINCT FROM NEW.command_json OR
     encode(sha256(NEW.command_canonical),'hex') IS DISTINCT FROM NEW.command_sha256 THEN
    RAISE EXCEPTION 'AQUB v2 command evidence is invalid or uncorrelated'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$$
"""

DELIVERY_INSERT_GUARD_TRIGGER_SQL = """
CREATE TRIGGER trg_aqub_v2_delivery_insert
BEFORE INSERT ON aqub_v2_delivery_ledger
FOR EACH ROW EXECUTE FUNCTION enforce_aqub_v2_delivery_insert()
"""

RECEIPT_VALIDATOR_SQL = """
CREATE FUNCTION assert_aqub_v2_delivery_receipt(
  p_command jsonb,p_receipt jsonb,p_ledger_state text
) RETURNS void LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE receipt_keys integer;
DECLARE receipt_status text;
BEGIN
  SELECT count(*) INTO receipt_keys FROM jsonb_object_keys(p_receipt);
  receipt_status := p_receipt->>'status';
  IF jsonb_typeof(p_receipt) IS DISTINCT FROM 'object' OR receipt_keys <> 12 OR
     jsonb_typeof(p_receipt->'schema_version') IS DISTINCT FROM 'string' OR
     p_receipt->>'schema_version' IS DISTINCT FROM 'aqub.delivery-receipt.v2' OR
     NOT (p_receipt ?& ARRAY['schema_version','receipt_ref','command_id',
       'idempotency_key','installation_ref','cube_ref','route_generation',
       'observed_at','status','retry_disposition','transport_receipt_ref']) OR
     p_receipt->>'command_id' IS DISTINCT FROM p_command->>'command_id' OR
     p_receipt->>'idempotency_key' IS DISTINCT FROM p_command->>'idempotency_key' OR
     p_receipt->>'installation_ref' IS DISTINCT FROM p_command->>'installation_ref' OR
     p_receipt->>'cube_ref' IS DISTINCT FROM p_command->>'cube_ref' OR
     p_receipt->>'route_generation' IS DISTINCT FROM p_command->>'route_generation' OR
     jsonb_typeof(p_receipt->'route_generation') IS DISTINCT FROM 'number' OR
     jsonb_typeof(p_receipt->'command_id') IS DISTINCT FROM 'string' OR
     jsonb_typeof(p_receipt->'idempotency_key') IS DISTINCT FROM 'string' OR
     jsonb_typeof(p_receipt->'installation_ref') IS DISTINCT FROM 'string' OR
     jsonb_typeof(p_receipt->'cube_ref') IS DISTINCT FROM 'string' OR
     jsonb_typeof(p_receipt->'receipt_ref') IS DISTINCT FROM 'string' OR
     COALESCE(p_receipt->>'receipt_ref' !~
       '^receipt:[A-Za-z][A-Za-z0-9._~-]{7,127}$',true) OR
     jsonb_typeof(p_receipt->'transport_receipt_ref') IS DISTINCT FROM 'string' OR
     COALESCE(p_receipt->>'transport_receipt_ref' !~
       '^receipt:[A-Za-z][A-Za-z0-9._~-]{7,127}$',true) OR
     jsonb_typeof(p_receipt->'observed_at') IS DISTINCT FROM 'string' OR
     COALESCE(p_receipt->>'observed_at' !~
       '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-5][0-9]\\.[0-9]{3}Z$',true) OR
     jsonb_typeof(p_receipt->'status') IS DISTINCT FROM 'string' OR
     jsonb_typeof(p_receipt->'retry_disposition') IS DISTINCT FROM 'string' OR
     (p_ledger_state='sent' AND (receipt_status IS DISTINCT FROM 'sent' OR
       p_receipt->>'retry_disposition' IS DISTINCT FROM 'do_not_retry' OR
       jsonb_typeof(p_receipt->'delivered_message_ref') IS DISTINCT FROM 'string' OR
       COALESCE(p_receipt->>'delivered_message_ref' !~
         '^message:[A-Za-z][A-Za-z0-9._~-]{7,127}$',true) OR
       p_receipt ?| ARRAY['failure_code','reconciliation_ref'])) OR
     (p_ledger_state IN ('failed','rejected') AND
      (receipt_status IS DISTINCT FROM 'failed' OR
       p_receipt->>'retry_disposition' IS DISTINCT FROM 'do_not_retry' OR
       jsonb_typeof(p_receipt->'failure_code') IS DISTINCT FROM 'string' OR
       COALESCE(p_receipt->>'failure_code' !~ '^[A-Z][A-Z0-9_]{2,95}$',true) OR
       p_receipt ?| ARRAY['delivered_message_ref','reconciliation_ref'])) OR
     (p_ledger_state='unknown' AND (receipt_status IS DISTINCT FROM 'unknown' OR
       p_receipt->>'retry_disposition' IS DISTINCT FROM 'reconcile_only' OR
       jsonb_typeof(p_receipt->'reconciliation_ref') IS DISTINCT FROM 'string' OR
       COALESCE(p_receipt->>'reconciliation_ref' !~
         '^reconcile:[A-Za-z][A-Za-z0-9._~-]{7,127}$',true) OR
       p_receipt ?| ARRAY['delivered_message_ref','failure_code'])) THEN
    RAISE EXCEPTION 'AQUB v2 receipt evidence is invalid or uncorrelated'
      USING ERRCODE='23514';
  END IF;
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
  IF TG_OP='INSERT' THEN
    IF NEW.state <> 'hold' OR NEW.authority_epoch <> 1 THEN
      RAISE EXCEPTION 'AQUB v2 route authority must start held at epoch one'
        USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
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
BEFORE INSERT OR UPDATE ON aqub_v2_route_authority
FOR EACH ROW EXECUTE FUNCTION enforce_aqub_v2_route_authority_transition()
"""

LEDGER_GUARD_STATEMENTS = (
    RECEIPT_VALIDATOR_SQL,
    DELIVERY_INSERT_GUARD_FUNCTION_SQL,
    DELIVERY_INSERT_GUARD_TRIGGER_SQL,
    DELIVERY_GUARD_FUNCTION_SQL,
    DELIVERY_GUARD_TRIGGER_SQL,
    ROUTE_GUARD_FUNCTION_SQL,
    ROUTE_GUARD_TRIGGER_SQL,
)

CAPABILITY_STATEMENTS = (
    """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gate_aqub_v2_owner') THEN
    CREATE ROLE gate_aqub_v2_owner NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gate_aqub_v2_runtime') THEN
    CREATE ROLE gate_aqub_v2_runtime NOLOGIN NOINHERIT;
  END IF;
END $$
""",
    "GRANT gate_aqub_v2_owner TO CURRENT_USER",
    "ALTER TABLE aqub_v2_route_authority OWNER TO gate_aqub_v2_owner",
    "ALTER TABLE aqub_v2_delivery_ledger OWNER TO gate_aqub_v2_owner",
    "ALTER FUNCTION assert_aqub_v2_delivery_receipt(jsonb,jsonb,text) OWNER TO gate_aqub_v2_owner",
    "ALTER FUNCTION enforce_aqub_v2_delivery_insert() OWNER TO gate_aqub_v2_owner",
    "ALTER FUNCTION enforce_aqub_v2_delivery_transition() OWNER TO gate_aqub_v2_owner",
    "ALTER FUNCTION enforce_aqub_v2_route_authority_transition() OWNER TO gate_aqub_v2_owner",
    """
DO $$ BEGIN
  EXECUTE format(
    'GRANT USAGE ON SCHEMA %I TO gate_aqub_v2_owner,gate_aqub_v2_runtime',
    current_schema()
  );
END $$
""",
    "REVOKE ALL ON aqub_v2_route_authority,aqub_v2_delivery_ledger FROM PUBLIC",
    "REVOKE ALL ON aqub_v2_route_authority,aqub_v2_delivery_ledger FROM CURRENT_USER",
    "GRANT SELECT ON aqub_v2_route_authority,aqub_v2_delivery_ledger TO gate_aqub_v2_runtime",
    "GRANT SELECT,INSERT,UPDATE ON "
    "aqub_v2_route_authority,aqub_v2_delivery_ledger TO gate_aqub_v2_owner",
    "REVOKE gate_aqub_v2_owner FROM CURRENT_USER",
)


def upgrade() -> None:
    op.execute(ROUTE_AUTHORITY_SQL)
    op.execute(DELIVERY_LEDGER_SQL)
    for statement in LEDGER_GUARD_STATEMENTS:
        op.execute(statement)
    for statement in CAPABILITY_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("GRANT gate_aqub_v2_owner TO CURRENT_USER")
    op.execute("DROP FUNCTION enforce_aqub_v2_route_authority_transition() CASCADE")
    op.execute("DROP FUNCTION enforce_aqub_v2_delivery_transition() CASCADE")
    op.execute("DROP FUNCTION enforce_aqub_v2_delivery_insert() CASCADE")
    op.execute("DROP FUNCTION assert_aqub_v2_delivery_receipt(jsonb,jsonb,text) CASCADE")
    op.drop_table("aqub_v2_delivery_ledger")
    op.drop_table("aqub_v2_route_authority")
    op.execute("REVOKE gate_aqub_v2_owner FROM CURRENT_USER")
