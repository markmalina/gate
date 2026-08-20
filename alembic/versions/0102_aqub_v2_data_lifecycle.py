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
         NEW.idempotency_key,NEW.command_sha256,NEW.state,NEW.created_at,
         NEW.consent_cancellation_id,NEW.consent_cancellation_ref,
         NEW.revoked_consent_ref,NEW.revoked_authority_epoch,
         NEW.consent_cancellation_receipt,NEW.consent_revoked_at)
       IS NOT DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.route_generation,
         OLD.idempotency_key,OLD.command_sha256,OLD.state,OLD.created_at,
         OLD.consent_cancellation_id,OLD.consent_cancellation_ref,
         OLD.revoked_consent_ref,OLD.revoked_authority_epoch,
         OLD.consent_cancellation_receipt,OLD.consent_revoked_at) THEN
    RETURN NEW;
  END IF;
  IF current_user='gate_aqub_v2_lifecycle_owner' AND
     OLD.content_redacted_at IS NULL AND NEW.content_redacted_at IS NULL AND
     OLD.consent_cancellation_id IS NULL AND
     NEW.consent_cancellation_id IS NOT NULL AND
     NEW.receipt_json IS NOT NULL AND NEW.settled_at IS NOT NULL AND
     NEW.updated_at IS NOT DISTINCT FROM NEW.consent_revoked_at AND
     ((OLD.state='committed' AND NEW.state='rejected' AND
       OLD.send_started_at IS NULL AND NEW.send_started_at IS NULL AND
       NEW.settled_at IS NOT DISTINCT FROM NEW.consent_revoked_at) OR
      (OLD.state='sending' AND NEW.state='unknown' AND
       OLD.send_started_at IS NOT NULL AND
       NEW.send_started_at IS NOT DISTINCT FROM OLD.send_started_at AND
       NEW.settled_at IS NOT DISTINCT FROM NEW.consent_revoked_at) OR
      (OLD.state='unknown' AND NEW.state='unknown' AND
       OLD.send_started_at IS NOT NULL AND
       NEW.send_started_at IS NOT DISTINCT FROM OLD.send_started_at AND
       NEW.settled_at IS NOT DISTINCT FROM OLD.settled_at)) AND
     ROW(NEW.id,NEW.command_id,NEW.installation_ref,NEW.conversation_ref,
         NEW.cube_ref,NEW.route_generation,NEW.idempotency_key,
         NEW.command_sha256,NEW.command_canonical,NEW.command_json,
         NEW.ack_ref,NEW.created_at,NEW.lifecycle_policy_version,
         NEW.lifecycle_consent_ref,NEW.lifecycle_receipt_ref,
         NEW.content_redacted_at)
       IS NOT DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.conversation_ref,
         OLD.cube_ref,OLD.route_generation,OLD.idempotency_key,
         OLD.command_sha256,OLD.command_canonical,OLD.command_json,
         OLD.ack_ref,OLD.created_at,OLD.lifecycle_policy_version,
         OLD.lifecycle_consent_ref,OLD.lifecycle_receipt_ref,
         OLD.content_redacted_at) THEN
    PERFORM {q}.assert_aqub_v2_delivery_receipt(
      NEW.command_json,NEW.receipt_json,NEW.state::text
    );
    PERFORM {q}.assert_aqub_v2_consent_cancellation(
      NEW.command_id,NEW.command_sha256,NEW.state::text,NEW.receipt_json,
      NEW.consent_cancellation_id,NEW.consent_cancellation_ref,
      NEW.revoked_consent_ref,NEW.revoked_authority_epoch,
      NEW.consent_revoked_at,NEW.consent_cancellation_receipt
    );
    RETURN NEW;
  END IF;
  IF current_user='gate_aqub_v2_lifecycle_owner' AND
     (NEW.state IS DISTINCT FROM OLD.state OR
      NEW.consent_cancellation_id IS DISTINCT FROM OLD.consent_cancellation_id OR
      NEW.consent_cancellation_ref IS DISTINCT FROM OLD.consent_cancellation_ref OR
      NEW.revoked_consent_ref IS DISTINCT FROM OLD.revoked_consent_ref OR
      NEW.revoked_authority_epoch IS DISTINCT FROM OLD.revoked_authority_epoch OR
      NEW.consent_cancellation_receipt IS DISTINCT FROM
        OLD.consent_cancellation_receipt OR
      NEW.consent_revoked_at IS DISTINCT FROM OLD.consent_revoked_at) THEN
    RAISE EXCEPTION 'AQUB v2 consent cancellation requires exact capability'
      USING ERRCODE='23514';
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
         NEW.ack_ref,NEW.created_at,NEW.consent_cancellation_id,
         NEW.consent_cancellation_ref,NEW.revoked_consent_ref,
         NEW.revoked_authority_epoch,NEW.consent_cancellation_receipt,
         NEW.consent_revoked_at)
     IS DISTINCT FROM
     ROW(OLD.id,OLD.command_id,OLD.installation_ref,OLD.conversation_ref,
         OLD.cube_ref,OLD.route_generation,OLD.idempotency_key,
         OLD.command_sha256,OLD.command_canonical,OLD.command_json,
         OLD.ack_ref,OLD.created_at,OLD.consent_cancellation_id,
         OLD.consent_cancellation_ref,OLD.revoked_consent_ref,
         OLD.revoked_authority_epoch,OLD.consent_cancellation_receipt,
         OLD.consent_revoked_at) THEN
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
    cancellation_validator = f"""
CREATE FUNCTION {q}.assert_aqub_v2_consent_cancellation(
  p_command_id uuid,
  p_command_sha256 text,
  p_state text,
  p_delivery_receipt jsonb,
  p_cancellation_id uuid,
  p_cancellation_ref text,
  p_revoked_consent_ref text,
  p_revoked_authority_epoch bigint,
  p_observed_at timestamptz,
  p_cancellation_receipt jsonb
) RETURNS void
LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  receipt_keys integer;
  expected_outcome text;
  expected_retry text;
BEGIN
  SELECT count(*) INTO receipt_keys
    FROM jsonb_object_keys(p_cancellation_receipt);
  expected_outcome := CASE p_state
    WHEN 'rejected' THEN 'cancelled_before_provider'
    WHEN 'unknown' THEN 'send_boundary_crossed_reconcile_only'
    ELSE NULL
  END;
  expected_retry := CASE p_state
    WHEN 'rejected' THEN 'do_not_retry'
    WHEN 'unknown' THEN 'reconcile_only'
    ELSE NULL
  END;
  IF p_cancellation_id IS NULL OR p_observed_at IS NULL OR
     p_observed_at IS DISTINCT FROM date_trunc('milliseconds',p_observed_at) OR
     p_command_sha256 !~ '^[0-9a-f]{{64}}$' OR
     p_revoked_authority_epoch IS NULL OR p_revoked_authority_epoch <= 0 OR
     p_revoked_consent_ref !~
       '^consent:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$' OR
     p_cancellation_ref IS DISTINCT FROM
       'cancel:a'||replace(p_cancellation_id::text,'-','') OR
     jsonb_typeof(p_cancellation_receipt) IS DISTINCT FROM 'object' OR
     receipt_keys <> 11 OR
     NOT (p_cancellation_receipt ?& ARRAY[
       'schema_version','cancellation_id','cancellation_ref','command_id',
       'command_sha256','revoked_consent_ref','revoked_authority_epoch',
       'observed_at','outcome','retry_disposition','delivery_receipt_ref'
     ]) OR
     p_cancellation_receipt->>'schema_version' IS DISTINCT FROM
       'gate.aqub-v2-consent-cancellation-receipt.v1' OR
     p_cancellation_receipt->>'cancellation_id' IS DISTINCT FROM
       p_cancellation_id::text OR
     p_cancellation_receipt->>'cancellation_ref' IS DISTINCT FROM
       p_cancellation_ref OR
     p_cancellation_receipt->>'command_id' IS DISTINCT FROM p_command_id::text OR
     p_cancellation_receipt->>'command_sha256' IS DISTINCT FROM
       p_command_sha256 OR
     p_cancellation_receipt->>'revoked_consent_ref' IS DISTINCT FROM
       p_revoked_consent_ref OR
     p_cancellation_receipt->>'revoked_authority_epoch' IS DISTINCT FROM
       p_revoked_authority_epoch::text OR
     jsonb_typeof(p_cancellation_receipt->'revoked_authority_epoch')
       IS DISTINCT FROM 'number' OR
     p_cancellation_receipt->>'observed_at' IS DISTINCT FROM to_char(
       p_observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
     ) OR
     p_cancellation_receipt->>'outcome' IS DISTINCT FROM expected_outcome OR
     p_cancellation_receipt->>'retry_disposition' IS DISTINCT FROM
       expected_retry OR
     p_cancellation_receipt->>'delivery_receipt_ref' IS DISTINCT FROM
       p_delivery_receipt->>'receipt_ref' THEN
    RAISE EXCEPTION 'AQUB v2 consent cancellation evidence is invalid'
      USING ERRCODE='23514';
  END IF;
END
$$
"""
    cancel_function = f"""
CREATE FUNCTION {q}.cancel_aqub_v2_delivery_for_revoked_consent(
  p_command_id uuid,
  p_expected_command_sha256 text,
  p_revoked_consent_ref text,
  p_revoked_authority_epoch bigint,
  p_cancellation_id uuid,
  p_cancellation_ref text,
  p_observed_at timestamptz
) RETURNS TABLE(
  result_status text,
  receipt_json jsonb,
  cancellation_receipt_json jsonb
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  delivery record;
  terminal_receipt jsonb;
  cancellation_receipt jsonb;
  outcome text;
  retry_disposition text;
  target_state text;
BEGIN
  IF p_command_id IS NULL OR p_cancellation_id IS NULL OR p_observed_at IS NULL OR
     p_observed_at IS DISTINCT FROM date_trunc('milliseconds',p_observed_at) OR
     p_expected_command_sha256 !~ '^[0-9a-f]{{64}}$' OR
     p_revoked_authority_epoch IS NULL OR p_revoked_authority_epoch <= 0 OR
     p_revoked_consent_ref !~
       '^consent:[A-Za-z][A-Za-z0-9._~-]{{7,127}}$' OR
     p_cancellation_ref IS DISTINCT FROM
       'cancel:a'||replace(p_cancellation_id::text,'-','') THEN
    RAISE EXCEPTION 'invalid AQUB v2 consent cancellation request'
      USING ERRCODE='22023';
  END IF;
  SELECT l.* INTO delivery
    FROM {q}.aqub_v2_delivery_ledger l
   WHERE l.command_id=p_command_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text,NULL::jsonb,NULL::jsonb;
    RETURN;
  END IF;
  IF delivery.command_sha256 IS DISTINCT FROM p_expected_command_sha256 THEN
    RAISE EXCEPTION 'AQUB v2 consent cancellation command fence failed'
      USING ERRCODE='55000';
  END IF;
  IF delivery.consent_cancellation_id IS NOT NULL THEN
    IF delivery.consent_cancellation_id IS DISTINCT FROM p_cancellation_id OR
       delivery.consent_cancellation_ref IS DISTINCT FROM p_cancellation_ref OR
       delivery.revoked_consent_ref IS DISTINCT FROM p_revoked_consent_ref OR
       delivery.revoked_authority_epoch IS DISTINCT FROM
         p_revoked_authority_epoch OR
       delivery.consent_revoked_at IS DISTINCT FROM p_observed_at THEN
      RAISE EXCEPTION 'AQUB v2 consent cancellation evidence conflict'
        USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT
      CASE WHEN delivery.state='rejected' THEN 'duplicate'
           WHEN delivery.state='unknown' THEN 'reconcile_only'
           ELSE 'terminal' END::text,
      delivery.receipt_json::jsonb,
      delivery.consent_cancellation_receipt::jsonb;
    RETURN;
  END IF;
  IF delivery.content_redacted_at IS NOT NULL THEN
    RETURN QUERY SELECT 'content_redacted'::text,NULL::jsonb,NULL::jsonb;
    RETURN;
  END IF;
  IF delivery.state='committed' AND delivery.send_started_at IS NULL THEN
    target_state := 'rejected';
    outcome := 'cancelled_before_provider';
    retry_disposition := 'do_not_retry';
    terminal_receipt := jsonb_build_object(
      'schema_version','aqub.delivery-receipt.v2',
      'receipt_ref','receipt:a'||encode(sha256(convert_to(
        'delivery:'||p_cancellation_id::text||':'||p_cancellation_ref,'UTF8'
      )),'hex'),
      'command_id',delivery.command_json->>'command_id',
      'idempotency_key',delivery.command_json->>'idempotency_key',
      'installation_ref',delivery.command_json->>'installation_ref',
      'cube_ref',delivery.command_json->>'cube_ref',
      'route_generation',(delivery.command_json->>'route_generation')::integer,
      'observed_at',to_char(
        p_observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
      ),
      'status','failed','retry_disposition','do_not_retry',
      'transport_receipt_ref','receipt:a'||encode(sha256(convert_to(
        'transport:'||p_cancellation_id::text||':'||p_cancellation_ref,'UTF8'
      )),'hex'),
      'failure_code','AQUB_V2_CONSENT_REVOKED'
    );
  ELSIF delivery.state='sending' THEN
    target_state := 'unknown';
    outcome := 'send_boundary_crossed_reconcile_only';
    retry_disposition := 'reconcile_only';
    terminal_receipt := jsonb_build_object(
      'schema_version','aqub.delivery-receipt.v2',
      'receipt_ref','receipt:a'||encode(sha256(convert_to(
        'delivery:'||p_cancellation_id::text||':'||p_cancellation_ref,'UTF8'
      )),'hex'),
      'command_id',delivery.command_json->>'command_id',
      'idempotency_key',delivery.command_json->>'idempotency_key',
      'installation_ref',delivery.command_json->>'installation_ref',
      'cube_ref',delivery.command_json->>'cube_ref',
      'route_generation',(delivery.command_json->>'route_generation')::integer,
      'observed_at',to_char(
        p_observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
      ),
      'status','unknown','retry_disposition','reconcile_only',
      'transport_receipt_ref','receipt:a'||encode(sha256(convert_to(
        'transport:'||p_cancellation_id::text||':'||p_cancellation_ref,'UTF8'
      )),'hex'),
      'reconciliation_ref','reconcile:a'||encode(sha256(convert_to(
        'reconcile:'||p_cancellation_id::text||':'||p_cancellation_ref,'UTF8'
      )),'hex')
    );
  ELSIF delivery.state='unknown' THEN
    target_state := 'unknown';
    outcome := 'send_boundary_crossed_reconcile_only';
    retry_disposition := 'reconcile_only';
    terminal_receipt := delivery.receipt_json;
  ELSE
    RETURN QUERY SELECT
      'terminal'::text,delivery.receipt_json::jsonb,NULL::jsonb;
    RETURN;
  END IF;
  cancellation_receipt := jsonb_build_object(
    'schema_version','gate.aqub-v2-consent-cancellation-receipt.v1',
    'cancellation_id',p_cancellation_id::text,
    'cancellation_ref',p_cancellation_ref,
    'command_id',p_command_id::text,
    'command_sha256',p_expected_command_sha256,
    'revoked_consent_ref',p_revoked_consent_ref,
    'revoked_authority_epoch',p_revoked_authority_epoch,
    'observed_at',to_char(
      p_observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ),
    'outcome',outcome,
    'retry_disposition',retry_disposition,
    'delivery_receipt_ref',terminal_receipt->>'receipt_ref'
  );
  UPDATE {q}.aqub_v2_delivery_ledger SET
    state=target_state,receipt_json=terminal_receipt,
    settled_at=COALESCE(settled_at,p_observed_at),updated_at=p_observed_at,
    consent_cancellation_id=p_cancellation_id,
    consent_cancellation_ref=p_cancellation_ref,
    revoked_consent_ref=p_revoked_consent_ref,
    revoked_authority_epoch=p_revoked_authority_epoch,
    consent_cancellation_receipt=cancellation_receipt,
    consent_revoked_at=p_observed_at
  WHERE id=delivery.id;
  RETURN QUERY SELECT
    CASE WHEN target_state='rejected' THEN 'cancelled'
         ELSE 'reconcile_only' END::text,
    terminal_receipt::jsonb,cancellation_receipt::jsonb;
END
$$
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
        "ADD COLUMN content_redacted_at timestamptz, "
        "ADD COLUMN consent_cancellation_id uuid, "
        "ADD COLUMN consent_cancellation_ref text, "
        "ADD COLUMN revoked_consent_ref text, "
        "ADD COLUMN revoked_authority_epoch bigint, "
        "ADD COLUMN consent_cancellation_receipt jsonb, "
        "ADD COLUMN consent_revoked_at timestamptz",
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
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger ADD CONSTRAINT "
        "uq_aqub_v2_delivery_consent_cancellation_id "
        "UNIQUE(consent_cancellation_id)",
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger ADD CONSTRAINT "
        "uq_aqub_v2_delivery_consent_cancellation_ref "
        "UNIQUE(consent_cancellation_ref)",
        f"ALTER TABLE {q}.aqub_v2_delivery_ledger ADD CONSTRAINT "
        "ck_aqub_v2_delivery_consent_cancellation CHECK "
        "((consent_cancellation_id IS NULL AND consent_cancellation_ref IS NULL "
        "AND revoked_consent_ref IS NULL AND revoked_authority_epoch IS NULL "
        "AND consent_cancellation_receipt IS NULL AND consent_revoked_at IS NULL) OR "
        "(consent_cancellation_id IS NOT NULL AND consent_cancellation_ref IS NOT NULL "
        "AND revoked_consent_ref IS NOT NULL "
        "AND revoked_authority_epoch IS NOT NULL AND revoked_authority_epoch > 0 "
        "AND consent_cancellation_receipt IS NOT NULL "
        "AND consent_revoked_at IS NOT NULL "
        "AND state IN ('rejected','unknown','sent','failed') "
        "AND (settled_at IS NOT NULL OR content_redacted_at IS NOT NULL) AND "
        "(receipt_json IS NOT NULL OR content_redacted_at IS NOT NULL)))",
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
        f"GRANT UPDATE(state,conversation_ref,cube_ref,command_canonical,command_json,"
        "ack_ref,receipt_json,send_started_at,settled_at,lifecycle_policy_version,"
        "lifecycle_consent_ref,lifecycle_receipt_ref,content_redacted_at,updated_at,"
        "consent_cancellation_id,consent_cancellation_ref,revoked_consent_ref,"
        "revoked_authority_epoch,consent_cancellation_receipt,consent_revoked_at) "
        f"ON {q}.aqub_v2_delivery_ledger TO gate_aqub_v2_lifecycle_owner",
        cancellation_validator,
        f"ALTER FUNCTION {q}.assert_aqub_v2_consent_cancellation("
        "uuid,text,text,jsonb,uuid,text,text,bigint,timestamptz,jsonb) "
        "OWNER TO gate_aqub_v2_lifecycle_owner",
        f"REVOKE ALL ON FUNCTION {q}.assert_aqub_v2_consent_cancellation("
        "uuid,text,text,jsonb,uuid,text,text,bigint,timestamptz,jsonb) "
        "FROM PUBLIC,gate_aqub_v2_runtime,gate_aqub_v2_lifecycle",
        f"GRANT EXECUTE ON FUNCTION {q}.assert_aqub_v2_delivery_receipt("
        "jsonb,jsonb,text) TO gate_aqub_v2_lifecycle_owner",
        delivery_transition_guard_statement(schema),
        cancel_function,
        f"ALTER FUNCTION {q}.cancel_aqub_v2_delivery_for_revoked_consent("
        "uuid,text,text,bigint,uuid,text,timestamptz) "
        "OWNER TO gate_aqub_v2_lifecycle_owner",
        f"REVOKE ALL ON FUNCTION {q}.cancel_aqub_v2_delivery_for_revoked_consent("
        "uuid,text,text,bigint,uuid,text,timestamptz) "
        "FROM PUBLIC,gate_aqub_v2_runtime",
        f"GRANT EXECUTE ON FUNCTION "
        f"{q}.cancel_aqub_v2_delivery_for_revoked_consent("
        "uuid,text,text,bigint,uuid,text,timestamptz) "
        "TO gate_aqub_v2_lifecycle",
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
            "WHERE content_redacted_at IS NOT NULL "
            "OR consent_cancellation_id IS NOT NULL)"
        )
    ).scalar_one()
    if redacted:
        raise RuntimeError("cannot downgrade AQUB v2 lifecycle with durable evidence")
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
    op.execute(
        f"DROP FUNCTION {q}.cancel_aqub_v2_delivery_for_revoked_consent("
        "uuid,text,text,bigint,uuid,text,timestamptz)"
    )
    op.execute(
        f"DROP FUNCTION {q}.assert_aqub_v2_consent_cancellation("
        "uuid,text,text,jsonb,uuid,text,text,bigint,timestamptz,jsonb)"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {q}.assert_aqub_v2_delivery_receipt("
        "jsonb,jsonb,text) FROM gate_aqub_v2_lifecycle_owner"
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
        "DROP COLUMN consent_cancellation_id, "
        "DROP COLUMN consent_cancellation_ref, "
        "DROP COLUMN revoked_consent_ref, "
        "DROP COLUMN revoked_authority_epoch, "
        "DROP COLUMN consent_cancellation_receipt, "
        "DROP COLUMN consent_revoked_at, "
        "ALTER COLUMN conversation_ref SET NOT NULL, "
        "ALTER COLUMN cube_ref SET NOT NULL, "
        "ALTER COLUMN command_canonical SET NOT NULL, "
        "ALTER COLUMN command_json SET NOT NULL, "
        "ALTER COLUMN ack_ref SET NOT NULL"
    )
    op.execute(
        f"REVOKE ALL ON {q}.aqub_v2_delivery_ledger "
        "FROM gate_aqub_v2_lifecycle_owner"
    )
    op.execute(
        f"REVOKE USAGE ON SCHEMA {q} FROM "
        "gate_aqub_v2_lifecycle_owner,gate_aqub_v2_lifecycle"
    )
    op.execute("REVOKE gate_aqub_v2_owner,gate_aqub_v2_lifecycle_owner FROM CURRENT_USER")
