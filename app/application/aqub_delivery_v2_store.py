"""SQL capability store for the default-off AQUB v2 delivery boundary.

Runtime code may execute the five reviewed SECURITY DEFINER operations only.
It never issues INSERT or UPDATE against the route or delivery tables.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_consent_cancellation_v1 import (
    AQUBV2ConsentCancellationError,
    parse_aqub_v2_consent_cancellation,
)
from app.application.aqub_contract_v2 import (
    AQUBV2Document,
    assert_delivery_correlation_v2,
    parse_delivery_ack_v2,
    parse_delivery_command_v2,
    parse_delivery_receipt_v2,
    serialize_contract_json_v2,
)

JsonObject = dict[str, Any]
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class AQUBV2DeliveryStoreError(RuntimeError):
    """A redacted fail-closed store error safe for the API boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _opaque(kind: str, value: str) -> str:
    return f"{kind}:a{hashlib.sha256(value.encode()).hexdigest()}"


def _ack(
    command: Mapping[str, Any],
    *,
    status: str,
    ack_ref: str,
    observed_at: datetime,
    reason_code: str | None,
) -> JsonObject:
    value: JsonObject = {
        "schema_version": "aqub.delivery-ack.v2",
        "ack_ref": ack_ref,
        "command_id": command["command_id"],
        "idempotency_key": command["idempotency_key"],
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
        "observed_at": _now_iso(observed_at),
        "status": status,
    }
    if reason_code is not None:
        value["reason_code"] = reason_code
    return parse_delivery_ack_v2(value).to_wire()


class AQUBV2DeliverySQLStore:
    """Call the SQL capability surface without direct runtime DML."""

    def __init__(self, session: AsyncSession, *, schema: str = "public") -> None:
        if _SCHEMA_RE.fullmatch(schema) is None:
            raise AQUBV2DeliveryStoreError("AQUB_V2_DATABASE_SCHEMA_INVALID")
        self._session = session
        self._schema = f'"{schema}"'

    async def accept(
        self,
        document: AQUBV2Document,
        *,
        observed_at: datetime | None = None,
    ) -> JsonObject:
        canonical = serialize_contract_json_v2(document)
        command_doc = parse_delivery_command_v2(document.to_wire())
        command = command_doc.to_wire()
        command_sha = hashlib.sha256(canonical).hexdigest()
        row_id = uuid4()
        proposed_ack_ref = _opaque("ack", str(row_id))
        now = observed_at or datetime.now(UTC)
        row = await self._one(
            f"""
            SELECT result_status,reason_code,ack_ref,receipt_json
              FROM {self._schema}.accept_aqub_v2_delivery(
                CAST(:row_id AS uuid),CAST(:command_json AS jsonb),
                CAST(:command_canonical AS bytea),:command_sha256,
                :ack_ref,:observed_at
              )
            """,
            {
                "row_id": str(row_id),
                "command_json": json.dumps(command, ensure_ascii=True, allow_nan=False),
                "command_canonical": canonical,
                "command_sha256": command_sha,
                "ack_ref": proposed_ack_ref,
                "observed_at": now,
            },
        )
        status = str(row["result_status"])
        reason = str(row["reason_code"]) if row["reason_code"] is not None else None
        ack = _ack(
            command,
            status=status,
            ack_ref=str(row["ack_ref"]),
            observed_at=now,
            reason_code=reason,
        )
        receipt = row["receipt_json"]
        if receipt is not None:
            receipt_doc = parse_delivery_receipt_v2(receipt)
            assert_delivery_correlation_v2(command_doc, receipt_doc)
            receipt = receipt_doc.to_wire()
        return {"ack": ack, "receipt": receipt}

    async def begin_dispatch(
        self,
        command_id: UUID,
        *,
        observed_at: datetime | None = None,
    ) -> JsonObject:
        row = await self._one(
            f"""
            SELECT result_status,command_json,receipt_json
              FROM {self._schema}.begin_aqub_v2_dispatch(
                CAST(:command_id AS uuid),:observed_at
              )
            """,
            {
                "command_id": str(command_id),
                "observed_at": observed_at or datetime.now(UTC),
            },
        )
        command = row["command_json"]
        if command is not None:
            command = parse_delivery_command_v2(command).to_wire()
        receipt = row["receipt_json"]
        if receipt is not None:
            receipt = parse_delivery_receipt_v2(receipt).to_wire()
        return {
            "kind": str(row["result_status"]),
            "command": command,
            "receipt": receipt,
        }

    async def settle(
        self,
        command_id: UUID,
        receipt_value: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> JsonObject:
        receipt = parse_delivery_receipt_v2(receipt_value).to_wire()
        row = await self._one(
            f"""
            SELECT result_status,receipt_json
              FROM {self._schema}.settle_aqub_v2_delivery(
                CAST(:command_id AS uuid),CAST(:receipt_json AS jsonb),:observed_at
              )
            """,
            {
                "command_id": str(command_id),
                "receipt_json": json.dumps(receipt, ensure_ascii=True, allow_nan=False),
                "observed_at": observed_at or datetime.now(UTC),
            },
        )
        returned = row["receipt_json"]
        return {
            "kind": str(row["result_status"]),
            "receipt": parse_delivery_receipt_v2(returned).to_wire()
            if returned is not None
            else None,
        }

    async def mark_unknown(
        self,
        command_id: UUID,
        *,
        observed_at: datetime | None = None,
    ) -> JsonObject:
        row = await self._one(
            f"""
            SELECT result_status,receipt_json
              FROM {self._schema}.mark_aqub_v2_delivery_unknown(
                CAST(:command_id AS uuid),:observed_at
              )
            """,
            {
                "command_id": str(command_id),
                "observed_at": observed_at or datetime.now(UTC),
            },
        )
        returned = row["receipt_json"]
        return {
            "kind": str(row["result_status"]),
            "receipt": parse_delivery_receipt_v2(returned).to_wire()
            if returned is not None
            else None,
        }

    async def reconcile_unknown(
        self,
        command_id: UUID,
        receipt_value: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> JsonObject:
        receipt = parse_delivery_receipt_v2(receipt_value).to_wire()
        row = await self._one(
            f"""
            SELECT result_status,receipt_json
              FROM {self._schema}.reconcile_aqub_v2_delivery_unknown(
                CAST(:command_id AS uuid),CAST(:receipt_json AS jsonb),:observed_at
              )
            """,
            {
                "command_id": str(command_id),
                "receipt_json": json.dumps(receipt, ensure_ascii=True, allow_nan=False),
                "observed_at": observed_at or datetime.now(UTC),
            },
        )
        returned = row["receipt_json"]
        return {
            "kind": str(row["result_status"]),
            "receipt": parse_delivery_receipt_v2(returned).to_wire()
            if returned is not None
            else None,
        }

    async def cancel_for_revoked_consent(
        self,
        cancellation_value: Mapping[str, Any],
    ) -> JsonObject:
        """Apply an authenticated revoke envelope before provider dispatch."""

        try:
            cancellation = parse_aqub_v2_consent_cancellation(cancellation_value)
        except AQUBV2ConsentCancellationError as exc:
            raise AQUBV2DeliveryStoreError(exc.code) from exc
        row = await self._one(
            f"""
            SELECT result_status,receipt_json,cancellation_receipt_json
              FROM {self._schema}.cancel_aqub_v2_delivery_for_revoked_consent(
                CAST(:command_id AS uuid),:command_sha256,
                :revoked_consent_ref,:revoked_authority_epoch,
                CAST(:cancellation_id AS uuid),:cancellation_ref,:observed_at
              )
            """,
            cancellation.sql_parameters(),
        )
        receipt = row["receipt_json"]
        if receipt is not None:
            receipt = parse_delivery_receipt_v2(receipt).to_wire()
        return {
            "kind": str(row["result_status"]),
            "receipt": receipt,
            "cancellation_receipt": row["cancellation_receipt_json"],
        }

    async def _one(self, statement: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            result = await self._session.execute(text(statement), parameters)
            row = result.mappings().one()
            await self._session.commit()
            return dict(row)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise AQUBV2DeliveryStoreError("AQUB_V2_DELIVERY_STORE_REFUSED") from exc
