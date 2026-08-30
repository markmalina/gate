"""Disposable PostgreSQL 17 durability rehearsal for the Gate neutral seam."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.gate_message_contract_v1 import (
    GateMessageAdapterMode,
    GateMessageContractError,
    parse_gate_message_object_v1,
)
from app.core.config import get_settings
from app.infrastructure.gate_message_preflight_store import (
    GateMessagePreflightStore,
    IngressDisposition,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "contracts" / "gate" / "v1" / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_disposable_postgres17_durable_ordering_and_fences() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL required for disposable Gate message rehearsal")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    schema = "gate31_rehearsal_" + uuid4().hex[:20]
    received_wire = _fixture("message-received.valid.json")
    command_wire = _fixture("message-send.valid.json")
    received = parse_gate_message_object_v1(received_wire)
    command = parse_gate_message_object_v1(command_wire)
    try:
        async with sessions() as session:
            version = await session.scalar(text("SHOW server_version_num"))
            assert str(version).startswith("17")
            store = GateMessagePreflightStore(
                session,
                schema=schema,
                mode=GateMessageAdapterMode.SYNTHETIC_REHEARSAL,
            )
            await store.initialize_disposable_schema()
            await store.activate_route(
                conversation_ref=received_wire["conversation_ref"],
                destination_ref=received_wire["destination_ref"],
                generation=1,
            )
            receipt = await store.commit_ingress_and_outbox(received, command)
            assert receipt.disposition is IngressDisposition.COMMITTED
            assert receipt.provider_ack_allowed is True
            assert await store.count_outbox() == 1

            replay = await store.commit_ingress_and_outbox(received, command)
            assert replay.disposition is IngressDisposition.DUPLICATE
            assert await store.count_outbox() == 1

            fact = parse_gate_message_object_v1(_fixture("delivery-fact.committed.json"))
            with pytest.raises(
                GateMessageContractError, match="CONSUMER_COMMIT_NOT_DURABLE"
            ):
                await store.record_delivery_fact(fact)
            await store.record_consumer_commit(
                command_ref=command_wire["command_ref"],
                commit_ref="commit:synthetic0001",
            )
            await store.record_delivery_fact(fact)

            with pytest.raises(GateMessageContractError, match="GENERATION_STALE"):
                await store.activate_route(
                    conversation_ref=received_wire["conversation_ref"],
                    destination_ref="destination:synthetic0002",
                    generation=3,
                )

        async with sessions() as session:
            stale = deepcopy(received_wire)
            stale["event_ref"] = "event:synthetic0002"
            stale["idempotency_key"] = "ingress/synthetic/0002"
            mismatched = deepcopy(command_wire)
            mismatched["command_ref"] = "command:synthetic0002"
            mismatched["destination_ref"] = "destination:synthetic0002"
            store = GateMessagePreflightStore(
                session,
                schema=schema,
                mode=GateMessageAdapterMode.SYNTHETIC_REHEARSAL,
            )
            with pytest.raises(GateMessageContractError, match="CROSS_ROUTE_REPLAY"):
                await store.commit_ingress_and_outbox(
                    parse_gate_message_object_v1(stale),
                    parse_gate_message_object_v1(mismatched),
                )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


def test_store_has_no_implicit_enablement_or_non_disposable_schema() -> None:
    with pytest.raises(GateMessageContractError, match="STORE_DISABLED"):
        GateMessagePreflightStore(object(), schema="gate31_rehearsal_0123456789abcdef")  # type: ignore[arg-type]
    with pytest.raises(GateMessageContractError, match="SCHEMA_INVALID"):
        GateMessagePreflightStore(
            object(),  # type: ignore[arg-type]
            schema="public",
            mode=GateMessageAdapterMode.SYNTHETIC_REHEARSAL,
        )
