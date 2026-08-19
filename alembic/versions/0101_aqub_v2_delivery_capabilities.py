"""Add the runtime-safe AQUB v2 delivery capability surface.

Revision ID: 0101_aqub_v2_delivery_capabilities
Revises: 0100_aqub_v2_delivery_ledger
"""

from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision = "0101_aqub_v2_delivery_capabilities"
down_revision = "0100_aqub_v2_delivery_ledger"
branch_labels = None
depends_on = None

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")

DELIVERY_TYPE_SQL = """
CREATE TYPE aqub_v2_delivery_type AS ENUM (
  'food.clarification',
  'food.fact_reply',
  'food.correction_reply',
  'food.day_summary',
  'food.omark_check'
)
"""

ADD_DELIVERY_TYPE_SQL = """
ALTER TABLE aqub_v2_delivery_ledger
  ADD COLUMN delivery_type aqub_v2_delivery_type
  GENERATED ALWAYS AS (
    (command_json #>> '{payload,delivery_type}')::aqub_v2_delivery_type
  ) STORED
"""

DELIVERY_INSERT_GUARD_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION enforce_aqub_v2_delivery_insert()
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
     command_keys <> 9 OR payload_keys <> 5 OR
     jsonb_typeof(NEW.command_json->'schema_version') IS DISTINCT FROM 'string' OR
     NEW.command_json->>'schema_version' IS DISTINCT FROM 'aqub.delivery-command.v2' OR
     NOT (NEW.command_json ?& ARRAY['schema_version','command_id','idempotency_key',
       'installation_ref','cube_ref','route_generation','conversation_ref',
       'requested_at','payload']) OR
     NOT (NEW.command_json->'payload' ?&
       ARRAY['delivery_type','kind','media_type','text','reply_to_message_ref']) OR
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
     jsonb_typeof(NEW.command_json->'payload'->'delivery_type') IS DISTINCT FROM 'string' OR
     NEW.command_json->'payload'->>'delivery_type' NOT IN (
       'food.clarification','food.fact_reply','food.correction_reply',
       'food.day_summary','food.omark_check') OR
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

LEGACY_DELIVERY_INSERT_GUARD_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION enforce_aqub_v2_delivery_insert()
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


def _quoted_schema(schema: str) -> str:
    if _SCHEMA_RE.fullmatch(schema) is None:
        raise RuntimeError("unsafe PostgreSQL schema name")
    return '"' + schema.replace('"', '""') + '"'


def capability_statements(schema: str) -> tuple[str, ...]:
    q = _quoted_schema(schema)
    accept_sql = f"""
CREATE FUNCTION {q}.accept_aqub_v2_delivery(
  p_row_id uuid,
  p_command_json jsonb,
  p_command_canonical bytea,
  p_command_sha256 text,
  p_ack_ref text,
  p_observed_at timestamptz
) RETURNS TABLE(
  result_status text,
  reason_code text,
  ack_ref text,
  receipt_json jsonb
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  existing record;
  current_route record;
  inserted_id uuid;
BEGIN
  IF p_observed_at IS NULL OR
     p_command_sha256 !~ '^[0-9a-f]{{64}}$' OR
     encode(sha256(p_command_canonical),'hex') IS DISTINCT FROM p_command_sha256 OR
     convert_from(p_command_canonical,'UTF8')::jsonb IS DISTINCT FROM p_command_json THEN
    RAISE EXCEPTION 'invalid AQUB v2 command capability evidence'
      USING ERRCODE='22023';
  END IF;

  SELECT l.* INTO existing
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.installation_ref=p_command_json->>'installation_ref'
     AND l.idempotency_key=p_command_json->>'idempotency_key'
   FOR SHARE;
  IF FOUND THEN
    IF existing.command_id::text IS DISTINCT FROM p_command_json->>'command_id' OR
       existing.command_sha256 IS DISTINCT FROM p_command_sha256 OR
       existing.command_canonical IS DISTINCT FROM p_command_canonical OR
       existing.delivery_type::text IS DISTINCT FROM
         p_command_json->'payload'->>'delivery_type' THEN
      RETURN QUERY SELECT
        'rejected'::text,'AQUB_V2_IDEMPOTENCY_CONFLICT'::text,
        p_ack_ref::text,NULL::jsonb;
    ELSE
      RETURN QUERY SELECT
        'duplicate'::text,NULL::text,existing.ack_ref::text,
        existing.receipt_json::jsonb;
    END IF;
    RETURN;
  END IF;

  SELECT r.* INTO current_route
    FROM {q}.aqub_v2_route_authority r
   WHERE r.installation_ref=p_command_json->>'installation_ref'
     AND r.conversation_ref=p_command_json->>'conversation_ref'
   FOR KEY SHARE;
  IF NOT FOUND OR current_route.state <> 'active' OR
     current_route.cube_ref IS DISTINCT FROM p_command_json->>'cube_ref' OR
     current_route.route_generation::text IS DISTINCT FROM
       p_command_json->>'route_generation' THEN
    RETURN QUERY SELECT
      'rejected'::text,'AQUB_V2_ROUTE_NOT_ACTIVE'::text,
      p_ack_ref::text,NULL::jsonb;
    RETURN;
  END IF;

  INSERT INTO {q}.aqub_v2_delivery_ledger(
    id,command_id,installation_ref,conversation_ref,cube_ref,
    route_generation,idempotency_key,command_sha256,command_canonical,
    command_json,state,ack_ref
  ) VALUES(
    p_row_id,(p_command_json->>'command_id')::uuid,
    p_command_json->>'installation_ref',
    p_command_json->>'conversation_ref',
    p_command_json->>'cube_ref',
    (p_command_json->>'route_generation')::integer,
    p_command_json->>'idempotency_key',
    p_command_sha256,p_command_canonical,p_command_json,'committed',p_ack_ref
  )
  ON CONFLICT(installation_ref,idempotency_key) DO NOTHING
  RETURNING id INTO inserted_id;

  IF inserted_id IS NOT NULL THEN
    RETURN QUERY SELECT
      'committed'::text,NULL::text,p_ack_ref::text,NULL::jsonb;
    RETURN;
  END IF;

  SELECT l.* INTO existing
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.installation_ref=p_command_json->>'installation_ref'
     AND l.idempotency_key=p_command_json->>'idempotency_key'
   FOR SHARE;
  IF existing.command_id::text IS DISTINCT FROM p_command_json->>'command_id' OR
     existing.command_sha256 IS DISTINCT FROM p_command_sha256 OR
     existing.command_canonical IS DISTINCT FROM p_command_canonical OR
     existing.delivery_type::text IS DISTINCT FROM
       p_command_json->'payload'->>'delivery_type' THEN
    RETURN QUERY SELECT
      'rejected'::text,'AQUB_V2_IDEMPOTENCY_CONFLICT'::text,
      p_ack_ref::text,NULL::jsonb;
  ELSE
    RETURN QUERY SELECT
      'duplicate'::text,NULL::text,existing.ack_ref::text,
      existing.receipt_json::jsonb;
  END IF;
END
$$
"""

    failed_receipt_sql = f"""
CREATE FUNCTION {q}.build_aqub_v2_failed_receipt(
  p_command jsonb,p_observed_at timestamptz,p_failure_code text
) RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'schema_version','aqub.delivery-receipt.v2',
    'receipt_ref','receipt:a'||replace(p_command->>'command_id','-',''),
    'command_id',p_command->>'command_id',
    'idempotency_key',p_command->>'idempotency_key',
    'installation_ref',p_command->>'installation_ref',
    'cube_ref',p_command->>'cube_ref',
    'route_generation',(p_command->>'route_generation')::integer,
    'observed_at',to_char(
      p_observed_at AT TIME ZONE 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ),
    'status','failed',
    'retry_disposition','do_not_retry',
    'transport_receipt_ref',
      'receipt:atransport'||replace(p_command->>'command_id','-',''),
    'failure_code',p_failure_code
  )
$$
"""

    unknown_receipt_sql = f"""
CREATE FUNCTION {q}.build_aqub_v2_unknown_receipt(
  p_command jsonb,p_observed_at timestamptz
) RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT jsonb_build_object(
    'schema_version','aqub.delivery-receipt.v2',
    'receipt_ref','receipt:a'||replace(p_command->>'command_id','-',''),
    'command_id',p_command->>'command_id',
    'idempotency_key',p_command->>'idempotency_key',
    'installation_ref',p_command->>'installation_ref',
    'cube_ref',p_command->>'cube_ref',
    'route_generation',(p_command->>'route_generation')::integer,
    'observed_at',to_char(
      p_observed_at AT TIME ZONE 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ),
    'status','unknown',
    'retry_disposition','reconcile_only',
    'transport_receipt_ref',
      'receipt:atransport'||replace(p_command->>'command_id','-',''),
    'reconciliation_ref','reconcile:a'||replace(p_command->>'command_id','-','')
  )
$$
"""

    begin_sql = f"""
CREATE FUNCTION {q}.begin_aqub_v2_dispatch(
  p_command_id uuid,p_observed_at timestamptz
) RETURNS TABLE(result_status text,command_json jsonb,receipt_json jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  delivery record;
  current_route record;
  rejected_receipt jsonb;
BEGIN
  SELECT l.* INTO delivery
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.command_id=p_command_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text,NULL::jsonb,NULL::jsonb;
    RETURN;
  END IF;
  IF delivery.state <> 'committed' THEN
    RETURN QUERY SELECT
      'not_dispatchable'::text,NULL::jsonb,delivery.receipt_json::jsonb;
    RETURN;
  END IF;

  SELECT r.* INTO current_route
    FROM {q}.aqub_v2_route_authority r
   WHERE r.installation_ref=delivery.installation_ref
     AND r.conversation_ref=delivery.conversation_ref
   FOR KEY SHARE;
  IF NOT FOUND OR current_route.state <> 'active' OR
     current_route.cube_ref IS DISTINCT FROM delivery.cube_ref OR
     current_route.route_generation IS DISTINCT FROM delivery.route_generation THEN
    rejected_receipt := {q}.build_aqub_v2_failed_receipt(
      delivery.command_json,p_observed_at,'AQUB_V2_ROUTE_NOT_ACTIVE'
    );
    UPDATE {q}.aqub_v2_delivery_ledger
       SET state='rejected',receipt_json=rejected_receipt,
           settled_at=p_observed_at,updated_at=p_observed_at
     WHERE id=delivery.id;
    RETURN QUERY SELECT
      'rejected'::text,NULL::jsonb,rejected_receipt::jsonb;
    RETURN;
  END IF;

  UPDATE {q}.aqub_v2_delivery_ledger
     SET state='sending',send_started_at=p_observed_at,updated_at=p_observed_at
   WHERE id=delivery.id;
  RETURN QUERY SELECT 'dispatch'::text,delivery.command_json::jsonb,NULL::jsonb;
END
$$
"""

    settle_sql = f"""
CREATE FUNCTION {q}.settle_aqub_v2_delivery(
  p_command_id uuid,p_receipt jsonb,p_observed_at timestamptz
) RETURNS TABLE(result_status text,receipt_json jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  delivery record;
  target_state text;
BEGIN
  SELECT l.* INTO delivery
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.command_id=p_command_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text,NULL::jsonb;
    RETURN;
  END IF;
  target_state := p_receipt->>'status';
  IF delivery.state IN ('sent','failed') AND
     delivery.state=target_state AND delivery.receipt_json=p_receipt THEN
    RETURN QUERY SELECT 'duplicate'::text,delivery.receipt_json::jsonb;
    RETURN;
  END IF;
  IF delivery.state <> 'sending' OR target_state NOT IN ('sent','failed') THEN
    RAISE EXCEPTION 'AQUB v2 delivery is not settleable'
      USING ERRCODE='55000';
  END IF;
  UPDATE {q}.aqub_v2_delivery_ledger
     SET state=target_state,receipt_json=p_receipt,
         settled_at=p_observed_at,updated_at=p_observed_at
   WHERE id=delivery.id;
  RETURN QUERY SELECT 'settled'::text,p_receipt::jsonb;
END
$$
"""

    mark_unknown_sql = f"""
CREATE FUNCTION {q}.mark_aqub_v2_delivery_unknown(
  p_command_id uuid,p_observed_at timestamptz
) RETURNS TABLE(result_status text,receipt_json jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  delivery record;
  unknown_receipt jsonb;
BEGIN
  SELECT l.* INTO delivery
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.command_id=p_command_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text,NULL::jsonb;
    RETURN;
  END IF;
  IF delivery.state='unknown' THEN
    RETURN QUERY SELECT 'duplicate'::text,delivery.receipt_json::jsonb;
    RETURN;
  END IF;
  IF delivery.state <> 'sending' THEN
    RAISE EXCEPTION 'AQUB v2 delivery did not cross the send boundary'
      USING ERRCODE='55000';
  END IF;
  unknown_receipt := {q}.build_aqub_v2_unknown_receipt(
    delivery.command_json,p_observed_at
  );
  UPDATE {q}.aqub_v2_delivery_ledger
     SET state='unknown',receipt_json=unknown_receipt,
         settled_at=p_observed_at,updated_at=p_observed_at
   WHERE id=delivery.id;
  RETURN QUERY SELECT 'unknown'::text,unknown_receipt::jsonb;
END
$$
"""

    reconcile_sql = f"""
CREATE FUNCTION {q}.reconcile_aqub_v2_delivery_unknown(
  p_command_id uuid,p_receipt jsonb,p_observed_at timestamptz
) RETURNS TABLE(result_status text,receipt_json jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  delivery record;
  target_state text;
BEGIN
  SELECT l.* INTO delivery
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.command_id=p_command_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text,NULL::jsonb;
    RETURN;
  END IF;
  target_state := p_receipt->>'status';
  IF delivery.state IN ('sent','failed') AND
     delivery.state=target_state AND delivery.receipt_json=p_receipt THEN
    RETURN QUERY SELECT 'duplicate'::text,delivery.receipt_json::jsonb;
    RETURN;
  END IF;
  IF delivery.state <> 'unknown' OR target_state NOT IN ('sent','failed') THEN
    RAISE EXCEPTION 'AQUB v2 delivery is not reconcilable'
      USING ERRCODE='55000';
  END IF;
  UPDATE {q}.aqub_v2_delivery_ledger
     SET state=target_state,receipt_json=p_receipt,
         settled_at=p_observed_at,updated_at=p_observed_at
   WHERE id=delivery.id;
  RETURN QUERY SELECT 'reconciled'::text,p_receipt::jsonb;
END
$$
"""

    functions = (
        failed_receipt_sql,
        unknown_receipt_sql,
        accept_sql,
        begin_sql,
        settle_sql,
        mark_unknown_sql,
        reconcile_sql,
    )
    ownership = (
        f"ALTER FUNCTION {q}.build_aqub_v2_failed_receipt(jsonb,timestamptz,text) "
        "OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.build_aqub_v2_unknown_receipt(jsonb,timestamptz) "
        "OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.accept_aqub_v2_delivery("
        "uuid,jsonb,bytea,text,text,timestamptz) OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.begin_aqub_v2_dispatch(uuid,timestamptz) "
        "OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.settle_aqub_v2_delivery(uuid,jsonb,timestamptz) "
        "OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.mark_aqub_v2_delivery_unknown(uuid,timestamptz) "
        "OWNER TO gate_aqub_v2_owner",
        f"ALTER FUNCTION {q}.reconcile_aqub_v2_delivery_unknown("
        "uuid,jsonb,timestamptz) OWNER TO gate_aqub_v2_owner",
    )
    revoke_public = tuple(
        statement.replace("ALTER FUNCTION", "REVOKE ALL ON FUNCTION").replace(
            " OWNER TO gate_aqub_v2_owner", " FROM PUBLIC"
        )
        for statement in ownership
    )
    runtime_grants = tuple(
        statement.replace("ALTER FUNCTION", "GRANT EXECUTE ON FUNCTION").replace(
            " OWNER TO gate_aqub_v2_owner", " TO gate_aqub_v2_runtime"
        )
        for statement in ownership[2:]
    )
    return functions + ownership + revoke_public + runtime_grants


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    op.execute("GRANT gate_aqub_v2_owner TO CURRENT_USER")
    op.execute(DELIVERY_TYPE_SQL)
    op.execute("ALTER TYPE aqub_v2_delivery_type OWNER TO gate_aqub_v2_owner")
    op.execute(ADD_DELIVERY_TYPE_SQL)
    op.execute("ALTER TABLE aqub_v2_delivery_ledger ALTER COLUMN delivery_type SET NOT NULL")
    op.execute(DELIVERY_INSERT_GUARD_FUNCTION_SQL)
    for statement in capability_statements(schema):
        op.execute(statement)
    op.execute("REVOKE gate_aqub_v2_owner FROM CURRENT_USER")


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    q = _quoted_schema(schema)
    op.execute("GRANT gate_aqub_v2_owner TO CURRENT_USER")
    for signature in (
        "reconcile_aqub_v2_delivery_unknown(uuid,jsonb,timestamptz)",
        "mark_aqub_v2_delivery_unknown(uuid,timestamptz)",
        "settle_aqub_v2_delivery(uuid,jsonb,timestamptz)",
        "begin_aqub_v2_dispatch(uuid,timestamptz)",
        "accept_aqub_v2_delivery(uuid,jsonb,bytea,text,text,timestamptz)",
        "build_aqub_v2_unknown_receipt(jsonb,timestamptz)",
        "build_aqub_v2_failed_receipt(jsonb,timestamptz,text)",
    ):
        op.execute(f"DROP FUNCTION {q}.{signature}")
    op.execute(LEGACY_DELIVERY_INSERT_GUARD_FUNCTION_SQL)
    op.execute("ALTER TABLE aqub_v2_delivery_ledger DROP COLUMN delivery_type")
    op.execute("DROP TYPE aqub_v2_delivery_type")
    op.execute("REVOKE gate_aqub_v2_owner FROM CURRENT_USER")
