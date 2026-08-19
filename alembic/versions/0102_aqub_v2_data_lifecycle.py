"""Add default-off AQUB v2 delivery data lifecycle controls.

Revision ID: 0102_aqub_v2_data_lifecycle
Revises: 0101_aqub_v2_capabilities
"""

from __future__ import annotations

import re
from pathlib import Path
from runpy import run_path

from alembic import op
from sqlalchemy import text

revision = "0102_aqub_v2_data_lifecycle"
down_revision = "0101_aqub_v2_capabilities"
branch_labels = None
depends_on = None

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


def _quoted_schema(schema: str) -> str:
    if _SCHEMA_RE.fullmatch(schema) is None:
        raise RuntimeError("unsafe PostgreSQL schema name")
    return '"' + schema.replace('"', '""') + '"'


def delivery_transition_guard_statement(schema: str) -> str:
    q = _quoted_schema(schema)
    return f"""
CREATE OR REPLACE FUNCTION {q}.enforce_aqub_v2_delivery_transition()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF current_user='gate_aqub_v2_lifecycle_owner' AND
     OLD.content_redacted_at IS NULL AND NEW.content_redacted_at IS NOT NULL AND
     OLD.state IN ('sent','failed','unknown','rejected') AND
     NEW.state=OLD.state AND NEW.command_canonical IS NULL AND
     NEW.command_json IS NULL AND NEW.conversation_ref IS NULL AND
     NEW.cube_ref IS NULL AND NEW.ack_ref IS NULL AND NEW.receipt_json IS NULL AND
     NEW.send_started_at IS NULL AND NEW.settled_at IS NULL AND
     NEW.lifecycle_policy_version IS NOT NULL AND
     NEW.lifecycle_consent_ref ~ '^consent:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$' AND
     NEW.lifecycle_receipt_ref ~ '^receipt:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$' AND
     ROW(NEW.id,NEW.command_id,NEW.installation_ref,NEW.route_generation,
         NEW.idempotency_key,NEW.command_sha256,NEW.state,NEW.created_at)
       IS NOT DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.route_generation,
         OLD.idempotency_key,OLD.command_sha256,OLD.state,OLD.created_at) THEN
    RETURN NEW;
  END IF;
  IF NEW.content_redacted_at IS DISTINCT FROM OLD.content_redacted_at OR
     NEW.lifecycle_policy_version IS DISTINCT FROM OLD.lifecycle_policy_version OR
     NEW.lifecycle_consent_ref IS DISTINCT FROM OLD.lifecycle_consent_ref OR
     NEW.lifecycle_receipt_ref IS DISTINCT FROM OLD.lifecycle_receipt_ref THEN
    RAISE EXCEPTION 'AQUB v2 lifecycle evidence is one-way and capability-only'
      USING ERRCODE='23514';
  END IF;
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
    PERFORM {q}.assert_aqub_v2_delivery_receipt(
      NEW.command_json,NEW.receipt_json,NEW.state::text
    );
  END IF;
  RETURN NEW;
END
$$
"""


def lifecycle_statements(schema: str) -> tuple[str, ...]:
    q = _quoted_schema(schema)
    policy_table = f"""
CREATE TABLE {q}.aqub_v2_lifecycle_policy (
  policy_version text PRIMARY KEY,
  policy_digest text NOT NULL CHECK (policy_digest ~ '^[0-9a-f]{{64}}$'),
  mode text NOT NULL CHECK (mode IN ('synthetic','approved')),
  enabled boolean NOT NULL DEFAULT false,
  policy_complete boolean NOT NULL DEFAULT false,
  retention_deadline timestamptz,
  tombstone_fields text[] NOT NULL DEFAULT '{{}}'::text[],
  policy_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (mode='synthetic' OR enabled=false),
  CHECK (NOT enabled OR (policy_complete AND retention_deadline IS NOT NULL))
)
"""
    receipt_table = f"""
CREATE TABLE {q}.aqub_v2_lifecycle_receipt (
  request_id uuid PRIMARY KEY,
  receipt_ref text NOT NULL UNIQUE CHECK (
    receipt_ref ~ '^receipt:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$'
  ),
  record_digest text NOT NULL CHECK (record_digest ~ '^[0-9a-f]{{64}}$'),
  command_id uuid NOT NULL,
  policy_version text NOT NULL REFERENCES {q}.aqub_v2_lifecycle_policy(policy_version),
  consent_ref text NOT NULL CHECK (
    consent_ref ~ '^consent:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$'
  ),
  prior_state text NOT NULL CHECK (prior_state IN ('sent','failed','unknown','rejected')),
  outcome text NOT NULL CHECK (outcome='redacted'),
  receipt_json jsonb NOT NULL,
  created_at timestamptz NOT NULL
)
"""
    redact_function = f"""
CREATE FUNCTION {q}.redact_aqub_v2_delivery(
  p_command_id uuid,
  p_expected_policy_version text,
  p_expected_consent_ref text,
  p_expected_command_sha256 text,
  p_expected_state text,
  p_expected_retention_deadline timestamptz,
  p_request_id uuid,
  p_observed_at timestamptz
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  policy record;
  delivery record;
  existing record;
  record_digest text;
  receipt_ref text;
  receipt jsonb;
BEGIN
  IF p_observed_at IS NULL OR p_expected_retention_deadline IS NULL OR
     p_expected_command_sha256 !~ '^[0-9a-f]{{64}}$' OR
     p_expected_state NOT IN ('sent','failed','unknown','rejected') OR
     p_expected_consent_ref !~ '^consent:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$' THEN
    RAISE EXCEPTION 'invalid AQUB v2 lifecycle request' USING ERRCODE='22023';
  END IF;
  record_digest := encode(sha256(convert_to(
    p_command_id::text||':'||p_expected_command_sha256||':'||p_expected_state,
    'UTF8'
  )),'hex');
  PERFORM pg_advisory_xact_lock(
    hashtextextended('gate-aqub-v2-lifecycle:'||record_digest,0)
  );
  SELECT * INTO existing FROM {q}.aqub_v2_lifecycle_receipt
   WHERE request_id=p_request_id FOR UPDATE;
  IF FOUND THEN
    IF existing.record_digest<>record_digest OR
       existing.command_id<>p_command_id OR
       existing.policy_version<>p_expected_policy_version OR
       existing.consent_ref<>p_expected_consent_ref OR
       existing.prior_state<>p_expected_state THEN
      RAISE EXCEPTION 'AQUB v2 lifecycle request is bound to other evidence'
        USING ERRCODE='42501';
    END IF;
    RETURN existing.receipt_json;
  END IF;
  SELECT * INTO policy FROM {q}.aqub_v2_lifecycle_policy
   WHERE policy_version=p_expected_policy_version FOR UPDATE;
  IF NOT FOUND OR NOT policy.enabled OR NOT policy.policy_complete OR
     policy.retention_deadline IS DISTINCT FROM p_expected_retention_deadline OR
     policy.retention_deadline>p_observed_at THEN
    RAISE EXCEPTION 'AQUB v2 lifecycle policy is default-off or not due'
      USING ERRCODE='55000';
  END IF;
  SELECT * INTO delivery FROM {q}.aqub_v2_delivery_ledger
   WHERE command_id=p_command_id FOR UPDATE;
  IF NOT FOUND OR delivery.command_sha256<>p_expected_command_sha256 OR
     delivery.state::text<>p_expected_state THEN
    RAISE EXCEPTION 'AQUB v2 lifecycle exact record fence failed'
      USING ERRCODE='55000';
  END IF;
  IF delivery.content_redacted_at IS NOT NULL THEN
    RAISE EXCEPTION 'AQUB v2 delivery was redacted by another request'
      USING ERRCODE='55000';
  END IF;
  receipt_ref := 'receipt:a'||replace(p_request_id::text,'-','');
  UPDATE {q}.aqub_v2_delivery_ledger SET
    conversation_ref=NULL,cube_ref=NULL,command_canonical=NULL,command_json=NULL,
    ack_ref=NULL,receipt_json=NULL,send_started_at=NULL,settled_at=NULL,
    lifecycle_policy_version=p_expected_policy_version,
    lifecycle_consent_ref=p_expected_consent_ref,
    lifecycle_receipt_ref=receipt_ref,content_redacted_at=p_observed_at,
    updated_at=p_observed_at
  WHERE id=delivery.id;
  receipt := jsonb_build_object(
    'schema_version','gate.aqub-v2-lifecycle-receipt.v1',
    'receipt_ref',receipt_ref,
    'policy_version',p_expected_policy_version,
    'record_digest',record_digest,
    'prior_state',p_expected_state,
    'outcome','redacted',
    'observed_at',to_char(
      p_observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ),
    'negative_verification','command_json_and_canonical_bytes_absent'
  );
  INSERT INTO {q}.aqub_v2_lifecycle_receipt(
    request_id,receipt_ref,record_digest,command_id,policy_version,consent_ref,
    prior_state,outcome,receipt_json,created_at
  ) VALUES(
    p_request_id,receipt_ref,record_digest,p_command_id,p_expected_policy_version,
    p_expected_consent_ref,p_expected_state,'redacted',receipt,p_observed_at
  );
  RETURN receipt;
END
$$
"""
    return (
        """
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='gate_aqub_v2_lifecycle_owner'
  ) THEN
    CREATE ROLE gate_aqub_v2_lifecycle_owner NOLOGIN NOSUPERUSER NOCREATEDB
      NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='gate_aqub_v2_lifecycle'
  ) THEN
    CREATE ROLE gate_aqub_v2_lifecycle NOLOGIN NOSUPERUSER NOCREATEDB
      NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
  END IF;
END $$
        """,
        "GRANT gate_aqub_v2_owner,gate_aqub_v2_lifecycle_owner TO CURRENT_USER",
        f"GRANT USAGE ON SCHEMA {q} TO "
        "gate_aqub_v2_lifecycle_owner,gate_aqub_v2_lifecycle",
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger "
        "ADD COLUMN lifecycle_policy_version text, "
        "ADD COLUMN lifecycle_consent_ref text, "
        "ADD COLUMN lifecycle_receipt_ref text, "
        "ADD COLUMN content_redacted_at timestamptz",
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger "
        "ALTER COLUMN conversation_ref DROP NOT NULL, "
        "ALTER COLUMN cube_ref DROP NOT NULL, "
        "ALTER COLUMN command_canonical DROP NOT NULL, "
        "ALTER COLUMN command_json DROP NOT NULL, "
        "ALTER COLUMN ack_ref DROP NOT NULL",
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger ADD CONSTRAINT "
        "ck_aqub_v2_delivery_lifecycle_content CHECK ((content_redacted_at IS NULL "
        "AND conversation_ref IS NOT NULL AND cube_ref IS NOT NULL "
        "AND command_canonical IS NOT NULL AND command_json IS NOT NULL "
        "AND ack_ref IS NOT NULL AND lifecycle_policy_version IS NULL "
        "AND lifecycle_consent_ref IS NULL AND lifecycle_receipt_ref IS NULL) OR "
        "(content_redacted_at IS NOT NULL AND conversation_ref IS NULL "
        "AND cube_ref IS NULL AND command_canonical IS NULL AND command_json IS NULL "
        "AND ack_ref IS NULL AND receipt_json IS NULL "
        "AND lifecycle_policy_version IS NOT NULL "
        "AND lifecycle_consent_ref IS NOT NULL AND lifecycle_receipt_ref IS NOT NULL))",
        policy_table,
        receipt_table,
        f"ALTER TABLE {q}.aqub_v2_lifecycle_policy "
        "OWNER TO gate_aqub_v2_lifecycle_owner",
        f"ALTER TABLE {q}.aqub_v2_lifecycle_receipt "
        "OWNER TO gate_aqub_v2_lifecycle_owner",
        f"REVOKE ALL ON {q}.aqub_v2_lifecycle_policy,"
        f"{q}.aqub_v2_lifecycle_receipt FROM PUBLIC,gate_aqub_v2_runtime",
        f"GRANT SELECT,INSERT,UPDATE ON {q}.aqub_v2_lifecycle_policy,"
        f"{q}.aqub_v2_lifecycle_receipt TO gate_aqub_v2_lifecycle_owner",
        f"GRANT SELECT ON {q}.aqub_v2_delivery_ledger "
        "TO gate_aqub_v2_lifecycle_owner",
        f"GRANT UPDATE(conversation_ref,cube_ref,command_canonical,command_json,"
        "ack_ref,receipt_json,send_started_at,settled_at,lifecycle_policy_version,"
        "lifecycle_consent_ref,lifecycle_receipt_ref,content_redacted_at,updated_at) "
        f"ON {q}.aqub_v2_delivery_ledger TO gate_aqub_v2_lifecycle_owner",
        delivery_transition_guard_statement(schema),
        redact_function,
        f"ALTER FUNCTION {q}.redact_aqub_v2_delivery("
        "uuid,text,text,text,text,timestamptz,uuid,timestamptz) "
        "OWNER TO gate_aqub_v2_lifecycle_owner",
        f"REVOKE ALL ON FUNCTION {q}.redact_aqub_v2_delivery("
        "uuid,text,text,text,text,timestamptz,uuid,timestamptz) "
        "FROM PUBLIC,gate_aqub_v2_runtime",
        f"GRANT EXECUTE ON FUNCTION {q}.redact_aqub_v2_delivery("
        "uuid,text,text,text,text,timestamptz,uuid,timestamptz) "
        "TO gate_aqub_v2_lifecycle",
        "REVOKE gate_aqub_v2_owner,gate_aqub_v2_lifecycle_owner FROM CURRENT_USER",
    )


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    for statement in lifecycle_statements(schema):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    q = _quoted_schema(schema)
    op.execute("GRANT gate_aqub_v2_owner,gate_aqub_v2_lifecycle_owner TO CURRENT_USER")
    redacted = bind.execute(
        text(
            f"SELECT EXISTS (SELECT 1 FROM {q}.aqub_v2_delivery_ledger "
            "WHERE content_redacted_at IS NOT NULL)"
        )
    ).scalar_one()
    if redacted:
        raise RuntimeError("cannot downgrade AQUB v2 lifecycle with redacted rows")
    previous = run_path(
        str(Path(__file__).with_name("0101_aqub_v2_delivery_capabilities.py"))
    )
    op.execute(
        previous["delivery_transition_guard_statement"](
            schema,
            qualify_receipt_validator=True,
        )
    )
    op.execute(
        f"DROP FUNCTION {q}.redact_aqub_v2_delivery("
        "uuid,text,text,text,text,timestamptz,uuid,timestamptz)"
    )
    op.execute(f"DROP TABLE {q}.aqub_v2_lifecycle_receipt")
    op.execute(f"DROP TABLE {q}.aqub_v2_lifecycle_policy")
    op.execute(
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger "
        "DROP CONSTRAINT ck_aqub_v2_delivery_lifecycle_content, "
        "DROP COLUMN lifecycle_policy_version, "
        "DROP COLUMN lifecycle_consent_ref, "
        "DROP COLUMN lifecycle_receipt_ref, "
        "DROP COLUMN content_redacted_at, "
        "ALTER COLUMN conversation_ref SET NOT NULL, "
        "ALTER COLUMN cube_ref SET NOT NULL, "
        "ALTER COLUMN command_canonical SET NOT NULL, "
        "ALTER COLUMN command_json SET NOT NULL, "
        "ALTER COLUMN ack_ref SET NOT NULL"
    )
    op.execute("REVOKE gate_aqub_v2_owner,gate_aqub_v2_lifecycle_owner FROM CURRENT_USER")
