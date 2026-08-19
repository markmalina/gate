"""Durability and fail-closed proofs for the default-off AQUB v2 seam."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from runpy import run_path
from uuid import UUID, uuid4

import pytest
from app.application.aqub_contract_v2 import (
    AQUBContractV2Error,
    AQUBV2Route,
    parse_delivery_command_v2,
)
from app.application.aqub_delivery_v2 import (
    AQUBV2DeliveryExecutor,
    AQUBV2DeliveryExecutorError,
    AQUBV2DeliveryMode,
    AQUBV2OutcomeUnknown,
)
from app.application.aqub_delivery_v2_store import (
    AQUBV2DeliverySQLStore,
    AQUBV2DeliveryStoreError,
)
from app.core.config import get_settings
from app.models.aqub_delivery_v2 import AQUBV2DeliveryLedger, AQUBV2RouteAuthority
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class Provider:
    def __init__(self, outcome: str = "sent") -> None:
        self.calls: list[dict[str, object]] = []
        self.outcome = outcome

    async def __call__(self, command: dict[str, object]) -> str:
        self.calls.append(command)
        if self.outcome == "unknown":
            raise AQUBV2OutcomeUnknown
        return "message:aopaque-result"


class SyntheticCrash(BaseException):
    pass


class CrashAfterProvider(Provider):
    async def __call__(self, command: dict[str, object]) -> str:
        self.calls.append(command)
        raise SyntheticCrash


def command(*, command_id: UUID | None = None, key: str = "food/reply/stable", text: str = "ok"):
    return {
        "schema_version": "aqub.delivery-command.v2",
        "command_id": str(command_id or uuid4()),
        "idempotency_key": key,
        "installation_ref": "installation:aopaque0",
        "cube_ref": "cube:aopaque0",
        "route_generation": 7,
        "conversation_ref": "conversation:aopaque0",
        "requested_at": "2026-08-19T12:00:00.000Z",
        "payload": {
            "delivery_type": "food.fact_reply",
            "kind": "text",
            "media_type": "text/plain",
            "text": text,
            "reply_to_message_ref": "message:aopaque0",
        },
    }


def receipt(delivery: dict[str, object], status: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "aqub.delivery-receipt.v2",
        "receipt_ref": "receipt:aopaque0",
        "command_id": delivery["command_id"],
        "idempotency_key": delivery["idempotency_key"],
        "installation_ref": delivery["installation_ref"],
        "cube_ref": delivery["cube_ref"],
        "route_generation": delivery["route_generation"],
        "observed_at": "2026-08-19T12:00:01.000Z",
        "status": status,
        "retry_disposition": "reconcile_only" if status == "unknown" else "do_not_retry",
        "transport_receipt_ref": "receipt:atransport0",
    }
    if status == "sent":
        value["delivered_message_ref"] = "message:aopaque0"
    elif status == "failed":
        value["failure_code"] = "PROVIDER_REJECTED"
    else:
        value["reconciliation_ref"] = "reconcile:aopaque0"
    return value


ROUTE = AQUBV2Route(
    installation_ref="installation:aopaque0",
    cube_ref="cube:aopaque0",
    route_generation=7,
)


async def schema_engine(database_url: str):
    schema = f"aqub_v2_delivery_{uuid4().hex}"
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
        migration = run_path("alembic/versions/0100_aqub_v2_delivery_ledger.py")
        for statement in migration["LEDGER_GUARD_STATEMENTS"]:
            await connection.exec_driver_sql(statement)
        for statement in migration["CAPABILITY_STATEMENTS"]:
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
        await connection.exec_driver_sql(
            "REVOKE gate_aqub_v2_owner FROM CURRENT_USER"
        )
    return schema, admin, engine


async def drop_schema(schema: str, admin, engine) -> None:
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await admin.dispose()


async def assert_sql_rejected(engine, statement: str, parameters: tuple[object, ...], match: str):
    async with engine.connect() as connection:
        transaction = await connection.begin()
        with pytest.raises(DBAPIError, match=match):
            await connection.exec_driver_sql(statement, parameters)
        await transaction.rollback()


def executor(session, provider: Provider, clock: Clock, *, enabled: bool = True):
    return AQUBV2DeliveryExecutor(
        session=session,
        provider=provider,
        clock=clock,
        mode=(
            AQUBV2DeliveryMode.SYNTHETIC_REHEARSAL
            if enabled
            else AQUBV2DeliveryMode.DISABLED
        ),
    )


async def seed_route(session) -> None:
    session.add(
        AQUBV2RouteAuthority(
            installation_ref=ROUTE.installation_ref,
            conversation_ref="conversation:aopaque0",
            cube_ref=ROUTE.cube_ref,
            route_generation=ROUTE.route_generation,
            state="hold",
            authority_epoch=1,
        )
    )
    await session.commit()
    await session.execute(
        update(AQUBV2RouteAuthority)
        .values(state="active", authority_epoch=2)
        .where(
            AQUBV2RouteAuthority.installation_ref == ROUTE.installation_ref,
            AQUBV2RouteAuthority.conversation_ref == "conversation:aopaque0",
        )
    )
    await session.commit()


@pytest.mark.anyio
async def test_default_off_and_strict_idempotency_have_zero_implicit_send() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    provider = Provider()
    clock = Clock()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        original = command()
        async with sessions() as session:
            await seed_route(session)
            with pytest.raises(AQUBV2DeliveryExecutorError, match="AQUB_V2_DELIVERY_DISABLED"):
                await executor(session, provider, clock, enabled=False).accept(original, ROUTE)
            assert await session.scalar(select(func.count()).select_from(AQUBV2DeliveryLedger)) == 0

        async with sessions() as session:
            first = await executor(session, provider, clock).accept(original, ROUTE)
            duplicate = await executor(session, provider, clock).accept(original, ROUTE)
            mutated = await executor(session, provider, clock).accept(
                command(key=original["idempotency_key"], text="changed"), ROUTE
            )
        assert first["ack"]["status"] == "committed"
        assert duplicate["ack"]["status"] == "duplicate"
        assert mutated["ack"]["status"] == "rejected"
        assert mutated["ack"]["reason_code"] == "AQUB_V2_IDEMPOTENCY_CONFLICT"
        assert provider.calls == []

        async with engine.connect() as connection:
            owner_result = await connection.exec_driver_sql(
                """SELECT tableowner FROM pg_tables
                     WHERE schemaname=current_schema()
                       AND tablename='aqub_v2_delivery_ledger'"""
            )
            table_owner = owner_result.scalar_one()
            runtime_can_insert = await connection.scalar(
                select(
                    func.has_table_privilege(
                        "gate_aqub_v2_runtime",
                        "aqub_v2_delivery_ledger",
                        "INSERT",
                    )
                )
            )
            runtime_can_update = await connection.scalar(
                select(
                    func.has_table_privilege(
                        "gate_aqub_v2_runtime",
                        "aqub_v2_delivery_ledger",
                        "UPDATE",
                    )
                )
            )
        assert table_owner == "gate_aqub_v2_owner"
        assert runtime_can_insert is False
        assert runtime_can_update is False

        await assert_sql_rejected(
            engine,
            """INSERT INTO aqub_v2_route_authority(
                   installation_ref,conversation_ref,cube_ref,
                   route_generation,state,authority_epoch
                 ) VALUES ($1,$2,$3,1,'active',1)""",
            (
                "installation:aforged0",
                "conversation:aforged0",
                "cube:aforged0",
            ),
            "must start held at epoch one",
        )

        forged = command(key="food/reply/forged-terminal")
        forged_canonical = json.dumps(
            forged,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        await assert_sql_rejected(
            engine,
            """INSERT INTO aqub_v2_delivery_ledger(
                   id,command_id,installation_ref,conversation_ref,cube_ref,
                   route_generation,idempotency_key,command_sha256,
                   command_canonical,command_json,state,ack_ref,settled_at,receipt_json
                 ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,'sent',$11,now(),'{}')""",
            (
                uuid4(),
                UUID(forged["command_id"]),
                forged["installation_ref"],
                forged["conversation_ref"],
                forged["cube_ref"],
                forged["route_generation"],
                forged["idempotency_key"],
                hashlib.sha256(forged_canonical).hexdigest(),
                forged_canonical,
                json.dumps(forged),
                "ack:aopaque-forged",
            ),
            "evidence-free committed state",
        )
        forged_hash = command(key="food/reply/forged-hash")
        forged_hash_canonical = json.dumps(
            forged_hash,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        await assert_sql_rejected(
            engine,
            """INSERT INTO aqub_v2_delivery_ledger(
                   id,command_id,installation_ref,conversation_ref,cube_ref,
                   route_generation,idempotency_key,command_sha256,
                   command_canonical,command_json,state,ack_ref
                 ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,'committed',$11)""",
            (
                uuid4(),
                UUID(forged_hash["command_id"]),
                forged_hash["installation_ref"],
                forged_hash["conversation_ref"],
                forged_hash["cube_ref"],
                forged_hash["route_generation"],
                forged_hash["idempotency_key"],
                "0" * 64,
                forged_hash_canonical,
                json.dumps(forged_hash),
                "ack:aopaque-forged-hash",
            ),
            "command evidence is invalid or uncorrelated",
        )
        command_null_cases = (
            ("schema", lambda value: value.__setitem__("schema_version", None)),
            ("route", lambda value: value.__setitem__("route_generation", None)),
            (
                "delivery-type",
                lambda value: value["payload"].__setitem__("delivery_type", None),
            ),
            ("kind", lambda value: value["payload"].__setitem__("kind", None)),
            ("text", lambda value: value["payload"].__setitem__("text", None)),
        )
        for label, mutate in command_null_cases:
            null_command = command(key=f"food/reply/null-{label}")
            mutate(null_command)
            null_canonical = json.dumps(
                null_command,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            await assert_sql_rejected(
                engine,
                """INSERT INTO aqub_v2_delivery_ledger(
                       id,command_id,installation_ref,conversation_ref,cube_ref,
                       route_generation,idempotency_key,command_sha256,
                       command_canonical,command_json,state,ack_ref
                     ) VALUES ($1,$2,$3,$4,$5,7,$6,$7,$8,$9::jsonb,'committed',$10)""",
                (
                    uuid4(),
                    UUID(null_command["command_id"]),
                    null_command["installation_ref"],
                    null_command["conversation_ref"],
                    null_command["cube_ref"],
                    null_command["idempotency_key"],
                    hashlib.sha256(null_canonical).hexdigest(),
                    null_canonical,
                    json.dumps(null_command),
                    f"ack:aopaque-null-{label}",
                ),
                "command evidence is invalid or uncorrelated",
            )

        await assert_sql_rejected(
            engine,
            """UPDATE aqub_v2_delivery_ledger
                  SET command_json=jsonb_set(command_json,'{payload,text}','\"tamper\"')
                WHERE command_id=$1""",
            (UUID(original["command_id"]),),
            "identity is immutable",
        )
        await assert_sql_rejected(
            engine,
            "UPDATE aqub_v2_delivery_ledger SET state='sent' WHERE command_id=$1",
            (UUID(original["command_id"]),),
            "invalid AQUB v2 delivery transition",
        )

        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """UPDATE aqub_v2_delivery_ledger
                      SET state='sending',send_started_at=now(),updated_at=now()
                    WHERE command_id=$1""",
                (UUID(original["command_id"]),),
            )
        await assert_sql_rejected(
            engine,
            """UPDATE aqub_v2_delivery_ledger
                  SET state='sent',settled_at=now(),receipt_json='{}',updated_at=now()
                WHERE command_id=$1""",
            (UUID(original["command_id"]),),
            "receipt evidence is invalid or uncorrelated",
        )

        receipt_null_cases = (
            ("sent", "observed_at"),
            ("sent", "transport_receipt_ref"),
            ("sent", "retry_disposition"),
            ("sent", "delivered_message_ref"),
            ("failed", "failure_code"),
            ("unknown", "reconciliation_ref"),
            ("unknown", "status"),
        )
        for terminal_state, field in receipt_null_cases:
            null_receipt = receipt(original, terminal_state)
            null_receipt[field] = None
            await assert_sql_rejected(
                engine,
                """UPDATE aqub_v2_delivery_ledger
                      SET state=$2,settled_at=now(),receipt_json=$3::jsonb,
                          updated_at=now()
                    WHERE command_id=$1""",
                (
                    UUID(original["command_id"]),
                    terminal_state,
                    json.dumps(null_receipt),
                ),
                "receipt evidence is invalid or uncorrelated",
            )

        raw = command(key="food/reply/raw-id")
        raw["payload"]["chat_id"] = "12345"
        async with sessions() as session:
            with pytest.raises(AQUBContractV2Error):
                await executor(session, provider, clock).accept(raw, ROUTE)
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_crash_before_send_dispatches_once_after_restart() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    provider = Provider()
    clock = Clock()
    delivery = command(key="food/reply/crash-before-send")
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as route_session:
            await seed_route(route_session)
        async with sessions() as session:
            accepted = await executor(session, provider, clock).accept(delivery, ROUTE)
        assert accepted["ack"]["status"] == "committed"
        assert provider.calls == []

        async with sessions() as restarted:
            sent = await executor(restarted, provider, clock).dispatch_committed(
                UUID(delivery["command_id"])
            )
        async with sessions() as replayed:
            duplicate = await executor(replayed, provider, clock).accept(delivery, ROUTE)
            no_resend = await executor(replayed, provider, clock).dispatch_committed(
                UUID(delivery["command_id"])
            )
        assert sent["kind"] == "sent"
        assert duplicate["ack"]["status"] == "duplicate"
        assert duplicate["receipt"] == sent["receipt"]
        assert no_resend["kind"] == "not_dispatchable"
        assert len(provider.calls) == 1
        await assert_sql_rejected(
            engine,
            """UPDATE aqub_v2_delivery_ledger
                  SET send_started_at=NULL
                WHERE command_id=$1""",
            (UUID(delivery["command_id"]),),
            "send boundary is irreversible",
        )
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_crash_after_provider_before_receipt_becomes_reconcile_only() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    provider = CrashAfterProvider()
    clock = Clock()
    delivery = command(key="food/reply/crash-after-provider")
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as route_session:
            await seed_route(route_session)
        async with sessions() as session:
            await executor(session, provider, clock).accept(delivery, ROUTE)
            with pytest.raises(SyntheticCrash):
                await executor(session, provider, clock).dispatch_committed(
                    UUID(delivery["command_id"])
                )
        clock.advance(31)
        async with sessions() as restarted:
            recovered = await executor(restarted, provider, clock).recover_stale_sending(
                UUID(delivery["command_id"])
            )
        async with sessions() as replayed:
            duplicate = await executor(replayed, provider, clock).accept(delivery, ROUTE)
            no_resend = await executor(replayed, provider, clock).dispatch_committed(
                UUID(delivery["command_id"])
            )
        assert recovered["kind"] == "unknown"
        assert recovered["receipt"]["retry_disposition"] == "reconcile_only"
        assert duplicate["receipt"] == recovered["receipt"]
        assert no_resend["kind"] == "not_dispatchable"
        assert len(provider.calls) == 1
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_route_hold_after_commit_rejects_before_provider() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    provider = Provider()
    clock = Clock()
    delivery = command(key="food/reply/route-hold")
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            await seed_route(session)
            await executor(session, provider, clock).accept(delivery, ROUTE)
            await session.execute(
                update(AQUBV2RouteAuthority)
                .values(state="hold", authority_epoch=3)
                .where(
                    AQUBV2RouteAuthority.installation_ref == ROUTE.installation_ref,
                    AQUBV2RouteAuthority.conversation_ref == "conversation:aopaque0",
                )
            )
            await session.commit()
            with pytest.raises(DBAPIError, match="authority epoch must increase"):
                await session.execute(
                    update(AQUBV2RouteAuthority)
                    .values(state="active", authority_epoch=3)
                    .where(
                        AQUBV2RouteAuthority.installation_ref == ROUTE.installation_ref,
                        AQUBV2RouteAuthority.conversation_ref == "conversation:aopaque0",
                    )
                )
                await session.commit()
            await session.rollback()
        async with sessions() as restarted:
            rejected = await executor(restarted, provider, clock).dispatch_committed(
                UUID(delivery["command_id"])
            )
        assert rejected["kind"] == "rejected"
        assert rejected["receipt"]["failure_code"] == "AQUB_V2_ROUTE_NOT_ACTIVE"
        assert provider.calls == []
    finally:
        await drop_schema(schema, admin, engine)


def test_v2_executor_has_no_runtime_or_provider_wiring() -> None:
    source = Path("app/application/aqub_delivery_v2.py").read_text(encoding="utf-8")
    assert "Telegram" not in source
    assert "httpx" not in source
    assert "os.environ" not in source
    assert "chat_id" not in source
    main_source = Path("app/main.py").read_text(encoding="utf-8")
    assert "app.application.aqub_delivery_v2 import" not in main_source
    assert "aqub_delivery_v2_internal_router" in main_source


@pytest.mark.anyio
async def test_sql_capability_store_isolated_route_idempotency_and_unknown() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as route_session:
            await seed_route(route_session)

        delivery = command(key="food/reply/capability")
        async with sessions() as session:
            store = AQUBV2DeliverySQLStore(session, schema=schema)
            first = await store.accept(parse_delivery_command_v2(delivery))
            duplicate = await store.accept(parse_delivery_command_v2(delivery))
            changed = command(
                command_id=UUID(delivery["command_id"]),
                key=delivery["idempotency_key"],
                text="changed",
            )
            conflict = await store.accept(parse_delivery_command_v2(changed))
            dispatch = await store.begin_dispatch(UUID(delivery["command_id"]))
            unknown = await store.mark_unknown(UUID(delivery["command_id"]))
            unknown_recovery = await store.accept(parse_delivery_command_v2(delivery))
            no_blind_resend = await store.begin_dispatch(UUID(delivery["command_id"]))

        assert first["ack"]["status"] == "committed"
        assert duplicate["ack"]["status"] == "duplicate"
        assert conflict["ack"]["status"] == "rejected"
        assert conflict["ack"]["reason_code"] == "AQUB_V2_IDEMPOTENCY_CONFLICT"
        assert dispatch["kind"] == "dispatch"
        assert unknown["kind"] == "unknown"
        assert unknown["receipt"]["retry_disposition"] == "reconcile_only"
        assert unknown_recovery["ack"]["status"] == "duplicate"
        assert unknown_recovery["receipt"] == unknown["receipt"]
        assert no_blind_resend["kind"] == "not_dispatchable"
        assert "conversation_ref" not in json.dumps(first, sort_keys=True)
        assert "conversation_ref" not in json.dumps(unknown, sort_keys=True)

        async with engine.connect() as connection:
            runtime_insert = await connection.scalar(
                select(
                    func.has_table_privilege(
                        "gate_aqub_v2_runtime",
                        f"{schema}.aqub_v2_delivery_ledger",
                        "INSERT",
                    )
                )
            )
            runtime_accept = await connection.scalar(
                select(
                    func.has_function_privilege(
                        "gate_aqub_v2_runtime",
                        (
                            f"{schema}.accept_aqub_v2_delivery"
                            "(uuid,jsonb,bytea,text,text,timestamptz)"
                        ),
                        "EXECUTE",
                    )
                )
            )
        assert runtime_insert is False
        assert runtime_accept is True
    finally:
        await drop_schema(schema, admin, engine)


@pytest.mark.anyio
async def test_capability_replay_recovers_terminal_and_sql_rejects_mismatch() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required")
    schema, admin, engine = await schema_engine(database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as route_session:
            await seed_route(route_session)

        delivery = command(key="food/reply/recovery-query")
        foreign = command(key="food/reply/foreign-receipt")
        command_id = UUID(delivery["command_id"])
        unknown_delivery = command(key="food/reply/recovery-unknown")
        unknown_command_id = UUID(unknown_delivery["command_id"])
        async with sessions() as session:
            store = AQUBV2DeliverySQLStore(session, schema=schema)
            await store.accept(parse_delivery_command_v2(delivery))
            await store.begin_dispatch(command_id)

            # The foreign receipt is schema-valid. Only the SQL update guard can
            # reject its correlation to the locked ledger row.
            with pytest.raises(
                AQUBV2DeliveryStoreError,
                match="AQUB_V2_DELIVERY_STORE_REFUSED",
            ):
                await store.settle(command_id, receipt(foreign, "sent"))

            terminal = await store.settle(command_id, receipt(delivery, "sent"))
            recovered = await store.accept(parse_delivery_command_v2(delivery))
            no_resend = await store.begin_dispatch(command_id)

            await store.accept(parse_delivery_command_v2(unknown_delivery))
            await store.begin_dispatch(unknown_command_id)
            await store.mark_unknown(unknown_command_id)
            with pytest.raises(
                AQUBV2DeliveryStoreError,
                match="AQUB_V2_DELIVERY_STORE_REFUSED",
            ):
                await store.reconcile_unknown(
                    unknown_command_id,
                    receipt(foreign, "failed"),
                )
            reconciled = await store.reconcile_unknown(
                unknown_command_id,
                receipt(unknown_delivery, "failed"),
            )
            recovered_reconciliation = await store.accept(
                parse_delivery_command_v2(unknown_delivery)
            )

        assert terminal["kind"] == "settled"
        assert recovered["ack"]["status"] == "duplicate"
        assert recovered["receipt"] == terminal["receipt"]
        assert no_resend["kind"] == "not_dispatchable"
        assert no_resend["receipt"] == terminal["receipt"]
        assert reconciled["kind"] == "reconciled"
        assert recovered_reconciliation["ack"]["status"] == "duplicate"
        assert recovered_reconciliation["receipt"] == reconciled["receipt"]
        assert "conversation_ref" not in json.dumps(recovered, sort_keys=True)
    finally:
        await drop_schema(schema, admin, engine)
