"""Disposable PostgreSQL store for the Gate message v1 preflight only.

The store refuses construction unless passed the code-owned synthetic mode and
a dedicated ``gate31_rehearsal_*`` schema.  Nothing imports it from runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.gate_message_contract_v1 import (
    GATE_MESSAGE_V1_MAX_RETRIES,
    GateMessageAdapterMode,
    GateMessageContractError,
    GateMessageDocument,
)

_SCHEMA = re.compile(r"^gate31_rehearsal_[0-9a-f]{16,32}$")


class IngressDisposition(StrEnum):
    COMMITTED = "committed"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class IngressReceipt:
    disposition: IngressDisposition
    event_ref: str
    command_ref: str
    provider_ack_allowed: bool


class GateMessagePreflightStore:
    def __init__(
        self,
        session: AsyncSession,
        *,
        schema: str,
        mode: GateMessageAdapterMode = GateMessageAdapterMode.DISABLED,
    ) -> None:
        if mode is not GateMessageAdapterMode.SYNTHETIC_REHEARSAL:
            raise GateMessageContractError("GATE_MESSAGE_STORE_DISABLED")
        if _SCHEMA.fullmatch(schema) is None:
            raise GateMessageContractError("GATE_MESSAGE_STORE_SCHEMA_INVALID")
        self._session = session
        self._schema = schema

    async def initialize_disposable_schema(self) -> None:
        q = self._quoted_schema
        await self._session.execute(text(f"CREATE SCHEMA {q}"))
        await self._session.execute(
            text(
                f"""
                CREATE TABLE {q}.routes (
                    conversation_ref text PRIMARY KEY,
                    destination_ref text NOT NULL,
                    generation integer NOT NULL CHECK (generation > 0)
                );
                CREATE TABLE {q}.ingress (
                    event_ref text PRIMARY KEY,
                    idempotency_key text NOT NULL UNIQUE,
                    conversation_ref text NOT NULL,
                    destination_ref text NOT NULL,
                    generation integer NOT NULL,
                    document jsonb NOT NULL,
                    committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
                );
                CREATE TABLE {q}.outbox (
                    command_ref text PRIMARY KEY,
                    ingress_event_ref text NOT NULL UNIQUE REFERENCES {q}.ingress(event_ref),
                    document jsonb NOT NULL,
                    attempt integer NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND {GATE_MESSAGE_V1_MAX_RETRIES}),
                    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','settled','unknown'))
                );
                CREATE TABLE {q}.consumer_commits (
                    command_ref text PRIMARY KEY REFERENCES {q}.outbox(command_ref),
                    commit_ref text NOT NULL UNIQUE
                );
                CREATE TABLE {q}.delivery_facts (
                    fact_ref text PRIMARY KEY,
                    command_ref text NOT NULL UNIQUE REFERENCES {q}.outbox(command_ref),
                    document jsonb NOT NULL
                )
                """
            )
        )
        await self._session.commit()

    async def activate_route(
        self, *, conversation_ref: str, destination_ref: str, generation: int
    ) -> None:
        q = self._quoted_schema
        existing = (
            await self._session.execute(
                text(
                    f"SELECT destination_ref, generation FROM {q}.routes "
                    "WHERE conversation_ref=:conversation FOR UPDATE"
                ),
                {"conversation": conversation_ref},
            )
        ).mappings().one_or_none()
        if existing is None:
            if generation != 1:
                raise GateMessageContractError("GATE_MESSAGE_ROUTE_GENERATION_STALE")
            await self._session.execute(
                text(
                    f"INSERT INTO {q}.routes(conversation_ref,destination_ref,generation) "
                    "VALUES (:conversation,:destination,:generation)"
                ),
                {
                    "conversation": conversation_ref,
                    "destination": destination_ref,
                    "generation": generation,
                },
            )
        elif generation != existing["generation"] + 1:
            raise GateMessageContractError("GATE_MESSAGE_ROUTE_GENERATION_STALE")
        else:
            await self._session.execute(
                text(
                    f"UPDATE {q}.routes SET destination_ref=:destination,generation=:generation "
                    "WHERE conversation_ref=:conversation"
                ),
                {
                    "conversation": conversation_ref,
                    "destination": destination_ref,
                    "generation": generation,
                },
            )
        await self._session.commit()

    async def commit_ingress_and_outbox(
        self, received: GateMessageDocument, command: GateMessageDocument
    ) -> IngressReceipt:
        incoming, outgoing = received.to_wire(), command.to_wire()
        if incoming["schema_version"] != "gate.message.received.v1":
            raise GateMessageContractError("GATE_MESSAGE_RECEIVED_REQUIRED")
        if outgoing["schema_version"] != "gate.message.send.v1":
            raise GateMessageContractError("GATE_MESSAGE_SEND_REQUIRED")
        for key in ("installation_ref", "conversation_ref", "destination_ref", "route_generation"):
            if incoming[key] != outgoing[key]:
                raise GateMessageContractError("GATE_MESSAGE_CROSS_ROUTE_REPLAY")
        await self._assert_current_route(incoming)
        q = self._quoted_schema
        duplicate = (
            await self._session.execute(
                text(
                    f"SELECT event_ref FROM {q}.ingress "
                    "WHERE idempotency_key=:key OR event_ref=:event"
                ),
                {"key": incoming["idempotency_key"], "event": incoming["event_ref"]},
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            await self._session.rollback()
            return IngressReceipt(
                IngressDisposition.DUPLICATE,
                str(duplicate),
                str(outgoing["command_ref"]),
                True,
            )
        await self._session.execute(
            text(
                f"INSERT INTO {q}.ingress "
                "(event_ref,idempotency_key,conversation_ref,destination_ref,generation,document) "
                "VALUES (:event,:key,:conversation,:destination,:generation,CAST(:document AS jsonb))"
            ),
            {
                "event": incoming["event_ref"],
                "key": incoming["idempotency_key"],
                "conversation": incoming["conversation_ref"],
                "destination": incoming["destination_ref"],
                "generation": incoming["route_generation"],
                "document": received.canonical_json.decode("ascii"),
            },
        )
        await self._session.execute(
            text(
                f"INSERT INTO {q}.outbox(command_ref,ingress_event_ref,document) "
                "VALUES (:command,:event,CAST(:document AS jsonb))"
            ),
            {
                "command": outgoing["command_ref"],
                "event": incoming["event_ref"],
                "document": command.canonical_json.decode("ascii"),
            },
        )
        await self._session.commit()
        return IngressReceipt(
            IngressDisposition.COMMITTED,
            str(incoming["event_ref"]),
            str(outgoing["command_ref"]),
            True,
        )

    async def record_consumer_commit(self, *, command_ref: str, commit_ref: str) -> None:
        await self._session.execute(
            text(
                f"INSERT INTO {self._quoted_schema}.consumer_commits(command_ref,commit_ref) "
                "VALUES (:command,:commit)"
            ),
            {"command": command_ref, "commit": commit_ref},
        )
        await self._session.commit()

    async def record_delivery_fact(self, fact: GateMessageDocument) -> None:
        wire = fact.to_wire()
        if wire["schema_version"] != "gate.delivery.fact.v1":
            raise GateMessageContractError("GATE_DELIVERY_FACT_REQUIRED")
        q = self._quoted_schema
        commit_ref = (
            await self._session.execute(
                text(
                    f"SELECT commit_ref FROM {q}.consumer_commits WHERE command_ref=:command"
                ),
                {"command": wire["command_ref"]},
            )
        ).scalar_one_or_none()
        if wire["status"] in {"committed", "duplicate"} and commit_ref != wire["consumer_commit_ref"]:
            await self._session.rollback()
            raise GateMessageContractError("GATE_MESSAGE_CONSUMER_COMMIT_NOT_DURABLE")
        await self._session.execute(
            text(
                f"INSERT INTO {q}.delivery_facts(fact_ref,command_ref,document) "
                "VALUES (:fact,:command,CAST(:document AS jsonb))"
            ),
            {
                "fact": wire["fact_ref"],
                "command": wire["command_ref"],
                "document": fact.canonical_json.decode("ascii"),
            },
        )
        state = "unknown" if wire["status"] == "unknown" else "settled"
        await self._session.execute(
            text(f"UPDATE {q}.outbox SET state=:state,attempt=:attempt WHERE command_ref=:command"),
            {"state": state, "attempt": wire["attempt"], "command": wire["command_ref"]},
        )
        await self._session.commit()

    async def count_outbox(self) -> int:
        value = await self._session.scalar(text(f"SELECT count(*) FROM {self._quoted_schema}.outbox"))
        return int(value or 0)

    async def _assert_current_route(self, wire: dict[str, Any]) -> None:
        route = (
            await self._session.execute(
                text(
                    f"SELECT destination_ref,generation FROM {self._quoted_schema}.routes "
                    "WHERE conversation_ref=:conversation"
                ),
                {"conversation": wire["conversation_ref"]},
            )
        ).mappings().one_or_none()
        if route is None or route["destination_ref"] != wire["destination_ref"]:
            raise GateMessageContractError("GATE_MESSAGE_DESTINATION_MISMATCH")
        if route["generation"] != wire["route_generation"]:
            raise GateMessageContractError("GATE_MESSAGE_ROUTE_GENERATION_STALE")

    @property
    def _quoted_schema(self) -> str:
        return '"' + self._schema + '"'
