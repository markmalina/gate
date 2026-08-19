"""Synthetic proofs for the default-off AQUB v2 data lifecycle capability."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from runpy import run_path
from uuid import UUID, uuid4

import pytest
from app.core.config import get_settings
from app.models.aqub_delivery_v2 import AQUBV2DeliveryLedger, AQUBV2RouteAuthority
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


def command(command_id: UUID) -> dict[str, object]:
    return {
        "schema_version": "aqub.delivery-command.v2",
        "command_id": str(command_id),
        "idempotency_key": f"food/reply/synthetic-{command_id.hex}",
        "installation_ref": "installation:asynthetic0",
        "cube_ref": "cube:asynthetic0",
        "route_generation": 1,
        "conversation_ref": "conversation:asynthetic0",
        "requested_at": "2026-08-19T12:00:00.000Z",
        "payload": {
            "delivery_type": "food.fact_reply",
            "kind": "text",
            "media_type": "text/plain",
            "text": "SYNTHETIC_GATE_FOOD_TEXT_ONLY",
            "reply_to_message_ref": None,
        },
    }


def unknown_receipt(document: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "aqub.delivery-receipt.v2",
        "receipt_ref": "receipt:asynthetic0",
        "command_id": document["command_id"],
        "idempotency_key": document["idempotency_key"],
        "installation_ref": document["installation_ref"],
        "cube_ref": document["cube_ref"],
        "route_generation": document["route_generation"],
        "observed_at": "2026-08-19T12:00:01.000Z",
        "status": "unknown",
        "retry_disposition": "reconcile_only",
        "transport_receipt_ref": "receipt:atransport0",
        "reconciliation_ref": "reconcile:asynthetic0",
    }


async def lifecycle_engine(database_url: str):
    schema = f"aqub_v2_lifecycle_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(AQUBV2RouteAuthority.__table__.create)
        await connection.run_sync(AQUBV2DeliveryLedger.__table__.create)
        await connection.exec_driver_sql(
            """ALTER TABLE aqub_v2_delivery_ledger
                 DROP CONSTRAINT ck_aqub_v2_delivery_lifecycle_content,
                 DROP COLUMN lifecycle_policy_version,
                 DROP COLUMN lifecycle_consent_ref,
                 DROP COLUMN lifecycle_receipt_ref,
                 DROP COLUMN content_redacted_at,
                 ALTER COLUMN conversation_ref SET NOT NULL,
                 ALTER COLUMN cube_ref SET NOT NULL,
                 ALTER COLUMN command_canonical SET NOT NULL,
                 ALTER COLUMN command_json SET NOT NULL,
                 ALTER COLUMN ack_ref SET NOT NULL"""
        )
        ledger = run_path("alembic/versions/0100_aqub_v2_delivery_ledger.py")
        for statement in ledger["LEDGER_GUARD_STATEMENTS"]:
            await connection.exec_driver_sql(statement)
        for statement in ledger["CAPABILITY_STATEMENTS"]:
            await connection.exec_driver_sql(statement)
        capabilities = run_path(
            "alembic/versions/0101_aqub_v2_delivery_capabilities.py"
        )
        await connection.exec_driver_sql("GRANT gate_aqub_v2_owner TO CURRENT_USER")
        await connection.exec_driver_sql(
            capabilities["DELIVERY_INSERT_GUARD_FUNCTION_SQL"]
        )
        for statement in capabilities["capability_statements"](schema):
            await connection.exec_driver_sql(statement)
        await connection.exec_driver_sql("REVOKE gate_aqub_v2_owner FROM CURRENT_USER")
        lifecycle = run_path("alembic/versions/0102_aqub_v2_data_lifecycle.py")
        for statement in lifecycle["lifecycle_statements"](schema):
            await connection.exec_driver_sql(statement)
    return schema, admin, engine


async def drop_schema(schema: str, admin, engine) -> None:
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.exec_driver_sql(
            "REVOKE gate_aqub_v2_lifecycle FROM CURRENT_USER"
        )
        await connection.exec_driver_sql(
            "REVOKE gate_aqub_v2_lifecycle_owner FROM CURRENT_USER"
        )
    await admin.dispose()


async def assert_rejected(engine, statement: str, parameters: tuple[object, ...]) -> None:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.exec_driver_sql("SET ROLE gate_aqub_v2_lifecycle")
        with pytest.raises(DBAPIError):
            await connection.exec_driver_sql(statement, parameters)
        await transaction.rollback()


@pytest.mark.anyio
async def test_redaction_is_default_off_idempotent_restart_safe_and_anti_replay() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await lifecycle_engine(database_url)
    command_id = uuid4()
    request_id = uuid4()
    other_command_id = uuid4()
    document = command(command_id)
    canonical = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    command_sha256 = hashlib.sha256(canonical).hexdigest()
    receipt = unknown_receipt(document)
    policy_version = "synthetic-gate-lifecycle-v1"
    consent_ref = "consent:asynthetic0"
    send_started_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    settled_at = datetime(2026, 8, 19, 12, 0, 1, tzinfo=UTC)
    retention_deadline = datetime(2026, 8, 19, 11, 59, tzinfo=UTC)
    observed_at = datetime(2026, 8, 19, 12, 2, tzinfo=UTC)
    function_sql = (
        f"SELECT {schema}.redact_aqub_v2_delivery("
        "$1,$2,$3,$4,$5,$6::timestamptz,$7,$8::timestamptz) AS receipt"
    )
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """INSERT INTO aqub_v2_delivery_ledger(
                     id,command_id,installation_ref,conversation_ref,cube_ref,
                     route_generation,idempotency_key,command_sha256,
                     command_canonical,command_json,state,ack_ref
                   ) VALUES($1,$2,$3,$4,$5,1,$6,$7,$8,$9::jsonb,'committed',$10)""",
                (
                    uuid4(),
                    command_id,
                    document["installation_ref"],
                    document["conversation_ref"],
                    document["cube_ref"],
                    document["idempotency_key"],
                    command_sha256,
                    canonical,
                    json.dumps(document),
                    "ack:asynthetic0",
                ),
            )
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_delivery_ledger
                      SET state='sending',send_started_at=$2::timestamptz,updated_at=$2
                    WHERE command_id=$1""",
                (command_id, send_started_at),
            )
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_delivery_ledger
                      SET state='unknown',receipt_json=$2::jsonb,
                          settled_at=$3::timestamptz,updated_at=$3
                    WHERE command_id=$1""",
                (command_id, json.dumps(receipt), settled_at),
            )
            await connection.exec_driver_sql(
                f"""INSERT INTO {schema}.aqub_v2_lifecycle_policy(
                     policy_version,policy_digest,mode,enabled,policy_complete,
                     retention_deadline,tombstone_fields,policy_json
                   ) VALUES($1,$2,'synthetic',false,true,$3::timestamptz,$4,$5::jsonb)""",
                (
                    policy_version,
                    "a" * 64,
                    retention_deadline,
                    ["synthetic-content-free"],
                    json.dumps({"synthetic": True, "real_data": False}),
                ),
            )
            await connection.exec_driver_sql(
                "GRANT gate_aqub_v2_lifecycle TO CURRENT_USER"
            )

        runtime_privilege_sql = (
            "SELECT has_function_privilege('gate_aqub_v2_runtime',"
            f"'{schema}.redact_aqub_v2_delivery("
            "uuid,text,text,text,text,timestamptz,uuid,timestamptz)','EXECUTE')"
        )
        async with engine.connect() as connection:
            runtime_privilege = await connection.exec_driver_sql(runtime_privilege_sql)
            assert runtime_privilege.scalar_one() is False
            runtime_membership = await connection.exec_driver_sql(
                """SELECT
                     pg_has_role(
                       'gate_aqub_v2_runtime',
                       'gate_aqub_v2_lifecycle_owner','USAGE'
                     ) AS owner,
                     pg_has_role(
                       'gate_aqub_v2_runtime',
                       'gate_aqub_v2_lifecycle','USAGE'
                     ) AS executor"""
            )
            assert runtime_membership.one() == (False, False)

        parameters = (
            command_id,
            policy_version,
            consent_ref,
            command_sha256,
            "unknown",
            retention_deadline,
            request_id,
            observed_at,
        )
        await assert_rejected(engine, function_sql, parameters)

        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "GRANT gate_aqub_v2_lifecycle_owner TO CURRENT_USER"
            )
            await connection.exec_driver_sql("SET ROLE gate_aqub_v2_lifecycle_owner")
            await connection.exec_driver_sql(
                f"UPDATE {schema}.aqub_v2_lifecycle_policy SET enabled=true "
                "WHERE policy_version=$1",
                (policy_version,),
            )
            await connection.exec_driver_sql("RESET ROLE")
            await connection.exec_driver_sql(
                "REVOKE gate_aqub_v2_lifecycle_owner FROM CURRENT_USER"
            )

        async with engine.begin() as connection:
            await connection.exec_driver_sql("SET ROLE gate_aqub_v2_lifecycle")
            first = (
                await connection.exec_driver_sql(function_sql, parameters)
            ).scalar_one()
            replay = (
                await connection.exec_driver_sql(
                    function_sql,
                    (*parameters[:-1], datetime(2026, 8, 19, 12, 3, tzinfo=UTC)),
                )
            ).scalar_one()
            await connection.exec_driver_sql("RESET ROLE")
        assert replay == first
        evidence = json.dumps(first, sort_keys=True)
        assert "SYNTHETIC_GATE_FOOD_TEXT_ONLY" not in evidence
        assert document["conversation_ref"] not in evidence
        assert document["cube_ref"] not in evidence

        await assert_rejected(
            engine,
            function_sql,
            (other_command_id, *parameters[1:]),
        )

        async with engine.connect() as connection:
            retained = (
                await connection.exec_driver_sql(
                    """SELECT installation_ref,idempotency_key,command_id,
                              command_sha256,state,conversation_ref,cube_ref,
                              command_json,command_canonical,ack_ref,receipt_json,
                              content_redacted_at
                         FROM aqub_v2_delivery_ledger WHERE command_id=$1""",
                    (command_id,),
                )
            ).mappings().one()
            assert retained["installation_ref"] == document["installation_ref"]
            assert retained["idempotency_key"] == document["idempotency_key"]
            assert retained["command_id"] == command_id
            assert retained["command_sha256"] == command_sha256
            assert retained["state"] == "unknown"
            for field in (
                "conversation_ref",
                "cube_ref",
                "command_json",
                "command_canonical",
                "ack_ref",
                "receipt_json",
            ):
                assert retained[field] is None
            assert retained["content_redacted_at"] is not None

            duplicate = (
                await connection.exec_driver_sql(
                    f"SELECT * FROM {schema}.accept_aqub_v2_delivery("
                    "$1,$2::jsonb,$3,$4,$5,$6::timestamptz)",
                    (
                        uuid4(),
                        json.dumps(document),
                        canonical,
                        command_sha256,
                        "ack:asynthetic1",
                        observed_at,
                    ),
                )
            ).mappings().one()
            assert duplicate["result_status"] == "rejected"
            assert duplicate["reason_code"] == "AQUB_V2_IDEMPOTENCY_CONFLICT"
            dispatch = (
                await connection.exec_driver_sql(
                    f"SELECT * FROM {schema}.begin_aqub_v2_dispatch("
                    "$1,$2::timestamptz)",
                    (command_id, observed_at),
                )
            ).mappings().one()
            assert dispatch["result_status"] == "not_dispatchable"
            assert dispatch["command_json"] is None

        restarted = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with restarted.connect() as connection:
                stored = (
                    await connection.exec_driver_sql(
                        f"SELECT receipt_json FROM {schema}.aqub_v2_lifecycle_receipt "
                        "WHERE request_id=$1",
                        (request_id,),
                    )
                ).scalar_one()
                assert stored == first
        finally:
            await restarted.dispose()
    finally:
        await drop_schema(schema, admin, engine)
