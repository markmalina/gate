"""Synthetic proofs for the default-off AQUB v2 data lifecycle capability."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from runpy import run_path
from uuid import UUID, uuid4

import pytest
from app.application.aqub_consent_cancellation_v1 import (
    AQUBV2ConsentCancellationError,
    cancellation_evidence_ref,
    parse_aqub_v2_consent_cancellation,
    serialize_aqub_v2_consent_cancellation,
)
from app.application.aqub_contract_v2 import AQUBV2Route
from app.application.aqub_delivery_v2 import (
    AQUBV2DeliveryExecutor,
    AQUBV2DeliveryExecutorError,
    AQUBV2DeliveryMode,
)
from app.application.aqub_delivery_v2_store import AQUBV2DeliverySQLStore
from app.core.config import get_settings
from app.models.aqub_delivery_v2 import AQUBV2DeliveryLedger, AQUBV2RouteAuthority
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
                 DROP COLUMN consent_cancellation_id,
                 DROP COLUMN consent_cancellation_ref,
                 DROP COLUMN revoked_consent_ref,
                 DROP COLUMN revoked_authority_epoch,
                 DROP COLUMN consent_cancellation_receipt,
                 DROP COLUMN consent_revoked_at,
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


def test_consent_cancellation_envelope_is_closed_canonical_and_deterministic() -> None:
    cancellation_id = UUID("00000000-0000-4000-8000-000000000001")
    envelope = {
        "schema_version": "aqub.consent-cancellation.v1",
        "cancellation_id": str(cancellation_id),
        "cancellation_ref": "cancel:a00000000000040008000000000000001",
        "command_id": "00000000-0000-4000-8000-000000000002",
        "command_sha256": "a" * 64,
        "revoked_consent_ref": "consent:arevokedevent0",
        "revoked_authority_epoch": 2,
        "observed_at": "2026-08-19T12:00:02.000Z",
    }
    parsed = parse_aqub_v2_consent_cancellation(envelope)
    assert parsed.to_wire() == envelope
    assert serialize_aqub_v2_consent_cancellation(envelope) == json.dumps(
        envelope,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert cancellation_evidence_ref(parsed, kind="delivery") == (
        "receipt:a9ab19a28cfef1416ec1e4aa233d8b157"
        "5103cbe5cb5fa8c82a3611405dbac39c"
    )
    assert cancellation_evidence_ref(parsed, kind="transport") == (
        "receipt:a5ce6489856990d4f4c1e753f8d37f95"
        "f1b3c45543612abc423f4e4b2d0cb81bb"
    )
    assert cancellation_evidence_ref(parsed, kind="reconcile") == (
        "reconcile:a7fe6d8a19b27d6867787b9437eb4bc4"
        "e858779d26d7d04987ae31cf2687f2851"
    )
    invalid_values = (
        dict(envelope, extra="forbidden"),
        dict(envelope, revoked_authority_epoch=True),
        dict(envelope, cancellation_ref="cancel:aforged000"),
        dict(envelope, observed_at="2026-08-19T12:00:02Z"),
        dict(envelope, observed_at="2026-08-19T12:00:02.000001Z"),
    )
    for invalid in invalid_values:
        with pytest.raises(AQUBV2ConsentCancellationError):
            parse_aqub_v2_consent_cancellation(invalid)


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


class ProviderSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, delivery: dict[str, object]) -> str:
        self.calls.append(delivery)
        return "message:ashould-not-send"


@pytest.mark.anyio
async def test_ack_then_exact_revoke_cancels_before_provider_and_survives_restart(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await lifecycle_engine(database_url)
    provider = ProviderSpy()
    command_id = uuid4()
    cancellation_id = uuid4()
    raw_sentinel = "RAW_FOOD_SENTINEL_MUST_NOT_ENTER_GATE_LOGS"
    document = command(command_id)
    document["payload"]["text"] = raw_sentinel
    route = AQUBV2Route(
        installation_ref=str(document["installation_ref"]),
        cube_ref=str(document["cube_ref"]),
        route_generation=int(document["route_generation"]),
    )
    cancellation_ref = f"cancel:a{str(cancellation_id).replace('-', '')}"
    observed_at = datetime(2026, 8, 19, 12, 0, 2, tzinfo=UTC)
    envelope: dict[str, object] = {
        "schema_version": "aqub.consent-cancellation.v1",
        "cancellation_id": str(cancellation_id),
        "cancellation_ref": cancellation_ref,
        "command_id": str(command_id),
        "command_sha256": "",
        "revoked_consent_ref": "consent:arevokedevent0",
        "revoked_authority_epoch": 2,
        "observed_at": "2026-08-19T12:00:02.000Z",
    }
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def executor(session) -> AQUBV2DeliveryExecutor:
        return AQUBV2DeliveryExecutor(
            session=session,
            provider=provider,
            mode=AQUBV2DeliveryMode.SYNTHETIC_REHEARSAL,
            clock=lambda: observed_at,
        )

    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """INSERT INTO aqub_v2_route_authority(
                     installation_ref,conversation_ref,cube_ref,
                     route_generation,state,authority_epoch
                   ) VALUES($1,$2,$3,1,'hold',1)""",
                (
                    document["installation_ref"],
                    document["conversation_ref"],
                    document["cube_ref"],
                ),
            )
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_route_authority
                      SET state='active',authority_epoch=2
                    WHERE installation_ref=$1 AND conversation_ref=$2""",
                (document["installation_ref"], document["conversation_ref"]),
            )
            await connection.exec_driver_sql(
                "GRANT gate_aqub_v2_lifecycle TO CURRENT_USER"
            )

        async with sessions() as session:
            accepted = await executor(session).accept(document, route)
            command_sha256 = (
                await session.execute(
                    text(
                        "SELECT command_sha256 FROM aqub_v2_delivery_ledger "
                        "WHERE command_id=:command_id"
                    ),
                    {"command_id": command_id},
                )
            ).scalar_one()
        assert accepted["ack"]["status"] == "committed"
        assert accepted["receipt"] is None
        envelope["command_sha256"] = command_sha256

        async with sessions() as cancellation_session:
            await cancellation_session.execute(
                text("SET ROLE gate_aqub_v2_lifecycle")
            )
            cancelled = await executor(cancellation_session).cancel_for_revoked_consent(
                envelope
            )
            await cancellation_session.execute(text("RESET ROLE"))
            await cancellation_session.commit()

        assert cancelled["kind"] == "cancelled"
        assert cancelled["receipt"]["status"] == "failed"
        assert cancelled["receipt"]["failure_code"] == "AQUB_V2_CONSENT_REVOKED"
        assert cancelled["receipt"]["retry_disposition"] == "do_not_retry"
        parsed_envelope = parse_aqub_v2_consent_cancellation(envelope)
        assert cancelled["receipt"]["receipt_ref"] == cancellation_evidence_ref(
            parsed_envelope,
            kind="delivery",
        )
        assert cancelled["receipt"]["transport_receipt_ref"] == (
            cancellation_evidence_ref(parsed_envelope, kind="transport")
        )
        assert cancelled["cancellation_receipt"] == {
            "schema_version": "gate.aqub-v2-consent-cancellation-receipt.v1",
            "cancellation_id": str(cancellation_id),
            "cancellation_ref": cancellation_ref,
            "command_id": str(command_id),
            "command_sha256": command_sha256,
            "revoked_consent_ref": envelope["revoked_consent_ref"],
            "revoked_authority_epoch": 2,
            "observed_at": envelope["observed_at"],
            "outcome": "cancelled_before_provider",
            "retry_disposition": "do_not_retry",
            "delivery_receipt_ref": cancelled["receipt"]["receipt_ref"],
        }

        async with sessions() as restarted_session:
            no_send = await executor(restarted_session).dispatch_committed(command_id)
            duplicate_accept = await executor(restarted_session).accept(document, route)
        assert no_send["kind"] == "not_dispatchable"
        assert no_send["state"] == "rejected"
        assert no_send["receipt"] == cancelled["receipt"]
        assert duplicate_accept["ack"]["status"] == "duplicate"
        assert duplicate_accept["receipt"] == cancelled["receipt"]
        assert provider.calls == []

        async with sessions() as replay_session:
            await replay_session.execute(text("SET ROLE gate_aqub_v2_lifecycle"))
            replay = await executor(replay_session).cancel_for_revoked_consent(envelope)
            forged_values = (
                dict(envelope, command_sha256="b" * 64),
                dict(envelope, revoked_authority_epoch=3),
                dict(envelope, observed_at="2026-08-19T12:00:03.000Z"),
            )
            for forged in forged_values:
                with pytest.raises(
                    AQUBV2DeliveryExecutorError,
                    match="AQUB_V2_CONSENT_CANCELLATION_REFUSED",
                ):
                    await executor(replay_session).cancel_for_revoked_consent(forged)
            await replay_session.execute(text("RESET ROLE"))
            await replay_session.commit()
        assert replay["kind"] == "duplicate"
        assert replay["receipt"] == cancelled["receipt"]
        assert replay["cancellation_receipt"] == cancelled["cancellation_receipt"]
        assert provider.calls == []

        serialized_evidence = json.dumps(cancelled, sort_keys=True)
        assert raw_sentinel not in serialized_evidence
        assert raw_sentinel not in caplog.text
        assert all(raw_sentinel not in record.getMessage() for record in caplog.records)

        async with engine.connect() as connection:
            persisted = (
                await connection.exec_driver_sql(
                    """SELECT state,send_started_at,consent_cancellation_id,
                              consent_cancellation_ref,revoked_consent_ref,
                              revoked_authority_epoch,consent_cancellation_receipt,
                              consent_revoked_at,receipt_json
                         FROM aqub_v2_delivery_ledger WHERE command_id=$1""",
                    (command_id,),
                )
            ).mappings().one()
        assert persisted["state"] == "rejected"
        assert persisted["send_started_at"] is None
        assert persisted["consent_cancellation_id"] == cancellation_id
        assert persisted["consent_cancellation_ref"] == cancellation_ref
        assert persisted["revoked_consent_ref"] == envelope["revoked_consent_ref"]
        assert persisted["revoked_authority_epoch"] == 2
        assert persisted["consent_cancellation_receipt"] == cancelled[
            "cancellation_receipt"
        ]
        assert persisted["consent_revoked_at"] is not None
        assert persisted["receipt_json"] == cancelled["receipt"]
        await assert_rejected(
            engine,
            f"SELECT * FROM {schema}.cancel_aqub_v2_delivery_for_revoked_consent("
            "$1,$2,$3,$4,$5,$6,$7::timestamptz)",
            (
                command_id,
                command_sha256,
                envelope["revoked_consent_ref"],
                2,
                cancellation_id,
                cancellation_ref,
                datetime(2026, 8, 19, 12, 0, 2, 1, tzinfo=UTC),
            ),
        )
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_revoke_after_send_boundary_becomes_reconcile_only_without_resend() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await lifecycle_engine(database_url)
    provider = ProviderSpy()
    command_id = uuid4()
    cancellation_id = uuid4()
    document = command(command_id)
    route = AQUBV2Route(
        installation_ref=str(document["installation_ref"]),
        cube_ref=str(document["cube_ref"]),
        route_generation=int(document["route_generation"]),
    )
    observed_at = datetime(2026, 8, 19, 12, 0, 2, tzinfo=UTC)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def executor(session) -> AQUBV2DeliveryExecutor:
        return AQUBV2DeliveryExecutor(
            session=session,
            provider=provider,
            mode=AQUBV2DeliveryMode.SYNTHETIC_REHEARSAL,
            clock=lambda: observed_at,
        )

    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """INSERT INTO aqub_v2_route_authority(
                     installation_ref,conversation_ref,cube_ref,
                     route_generation,state,authority_epoch
                   ) VALUES($1,$2,$3,1,'hold',1)""",
                (
                    document["installation_ref"],
                    document["conversation_ref"],
                    document["cube_ref"],
                ),
            )
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_route_authority
                      SET state='active',authority_epoch=2
                    WHERE installation_ref=$1 AND conversation_ref=$2""",
                (document["installation_ref"], document["conversation_ref"]),
            )
            await connection.exec_driver_sql(
                "GRANT gate_aqub_v2_lifecycle TO CURRENT_USER"
            )

        async with sessions() as session:
            await executor(session).accept(document, route)
            command_sha256 = (
                await session.execute(
                    text(
                        "SELECT command_sha256 FROM aqub_v2_delivery_ledger "
                        "WHERE command_id=:command_id"
                    ),
                    {"command_id": command_id},
                )
            ).scalar_one()
            crossed = await AQUBV2DeliverySQLStore(
                session,
                schema=schema,
            ).begin_dispatch(command_id, observed_at=observed_at)
        assert crossed["kind"] == "dispatch"

        cancellation_ref = f"cancel:a{str(cancellation_id).replace('-', '')}"
        envelope = {
            "schema_version": "aqub.consent-cancellation.v1",
            "cancellation_id": str(cancellation_id),
            "cancellation_ref": cancellation_ref,
            "command_id": str(command_id),
            "command_sha256": command_sha256,
            "revoked_consent_ref": "consent:arevokedevent1",
            "revoked_authority_epoch": 4,
            "observed_at": "2026-08-19T12:00:02.000Z",
        }
        async with sessions() as cancellation_session:
            await cancellation_session.execute(
                text("SET ROLE gate_aqub_v2_lifecycle")
            )
            reconciled_only = await executor(
                cancellation_session
            ).cancel_for_revoked_consent(envelope)
            await cancellation_session.execute(text("RESET ROLE"))
            await cancellation_session.commit()

        assert reconciled_only["kind"] == "reconcile_only"
        assert reconciled_only["receipt"]["status"] == "unknown"
        assert reconciled_only["receipt"]["retry_disposition"] == "reconcile_only"
        parsed_envelope = parse_aqub_v2_consent_cancellation(envelope)
        assert reconciled_only["receipt"]["receipt_ref"] == cancellation_evidence_ref(
            parsed_envelope,
            kind="delivery",
        )
        assert reconciled_only["receipt"]["transport_receipt_ref"] == (
            cancellation_evidence_ref(parsed_envelope, kind="transport")
        )
        assert reconciled_only["receipt"]["reconciliation_ref"] == (
            cancellation_evidence_ref(parsed_envelope, kind="reconcile")
        )
        assert reconciled_only["cancellation_receipt"]["outcome"] == (
            "send_boundary_crossed_reconcile_only"
        )
        assert reconciled_only["cancellation_receipt"]["revoked_authority_epoch"] == 4

        async with sessions() as restarted_session:
            no_blind_resend = await executor(restarted_session).dispatch_committed(
                command_id
            )
        assert no_blind_resend["kind"] == "not_dispatchable"
        assert no_blind_resend["state"] == "unknown"
        assert no_blind_resend["receipt"] == reconciled_only["receipt"]
        assert provider.calls == []
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_preexisting_unknown_receipt_is_preserved_and_bound_to_revoke() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await lifecycle_engine(database_url)
    provider = ProviderSpy()
    command_id = uuid4()
    cancellation_id = uuid4()
    document = command(command_id)
    route = AQUBV2Route(
        installation_ref=str(document["installation_ref"]),
        cube_ref=str(document["cube_ref"]),
        route_generation=int(document["route_generation"]),
    )
    observed_at = datetime(2026, 8, 19, 12, 0, 2, tzinfo=UTC)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def executor(session) -> AQUBV2DeliveryExecutor:
        return AQUBV2DeliveryExecutor(
            session=session,
            provider=provider,
            mode=AQUBV2DeliveryMode.SYNTHETIC_REHEARSAL,
            clock=lambda: observed_at,
        )

    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """INSERT INTO aqub_v2_route_authority(
                     installation_ref,conversation_ref,cube_ref,
                     route_generation,state,authority_epoch
                   ) VALUES($1,$2,$3,1,'hold',1)""",
                (
                    document["installation_ref"],
                    document["conversation_ref"],
                    document["cube_ref"],
                ),
            )
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_route_authority
                      SET state='active',authority_epoch=2
                    WHERE installation_ref=$1 AND conversation_ref=$2""",
                (document["installation_ref"], document["conversation_ref"]),
            )
            await connection.exec_driver_sql(
                "GRANT gate_aqub_v2_lifecycle TO CURRENT_USER"
            )

        async with sessions() as session:
            await executor(session).accept(document, route)
            command_sha256 = (
                await session.execute(
                    text(
                        "SELECT command_sha256 FROM aqub_v2_delivery_ledger "
                        "WHERE command_id=:command_id"
                    ),
                    {"command_id": command_id},
                )
            ).scalar_one()
            store = AQUBV2DeliverySQLStore(session, schema=schema)
            crossed = await store.begin_dispatch(command_id, observed_at=observed_at)
            existing_unknown = await store.mark_unknown(
                command_id,
                observed_at=observed_at,
            )
        assert crossed["kind"] == "dispatch"
        assert existing_unknown["kind"] == "unknown"

        cancellation_ref = f"cancel:a{str(cancellation_id).replace('-', '')}"
        envelope = {
            "schema_version": "aqub.consent-cancellation.v1",
            "cancellation_id": str(cancellation_id),
            "cancellation_ref": cancellation_ref,
            "command_id": str(command_id),
            "command_sha256": command_sha256,
            "revoked_consent_ref": "consent:arevokedevent2",
            "revoked_authority_epoch": 5,
            "observed_at": "2026-08-19T12:00:02.000Z",
        }
        async with sessions() as cancellation_session:
            await cancellation_session.execute(
                text("SET ROLE gate_aqub_v2_lifecycle")
            )
            cancellation = await executor(
                cancellation_session
            ).cancel_for_revoked_consent(envelope)
            await cancellation_session.execute(text("RESET ROLE"))
            await cancellation_session.commit()

        assert cancellation["kind"] == "reconcile_only"
        assert cancellation["receipt"] == existing_unknown["receipt"]
        assert cancellation["receipt"]["retry_disposition"] == "reconcile_only"
        assert cancellation["cancellation_receipt"]["delivery_receipt_ref"] == (
            existing_unknown["receipt"]["receipt_ref"]
        )
        assert cancellation["cancellation_receipt"]["outcome"] == (
            "send_boundary_crossed_reconcile_only"
        )
        assert provider.calls == []

        async with sessions() as restarted_session:
            no_resend = await executor(restarted_session).dispatch_committed(command_id)
        assert no_resend["kind"] == "not_dispatchable"
        assert no_resend["state"] == "unknown"
        assert no_resend["receipt"] == existing_unknown["receipt"]
    finally:
        await drop_schema(schema, admin, engine)
