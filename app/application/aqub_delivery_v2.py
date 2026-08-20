"""Default-off durable AQUB v2 delivery executor with no runtime wiring."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_consent_cancellation_v1 import (
    AQUBV2ConsentCancellationError,
    parse_aqub_v2_consent_cancellation,
)
from app.application.aqub_contract_v2 import (
    AQUBV2Route,
    assert_delivery_correlation_v2,
    assert_route_match_v2,
    parse_delivery_ack_v2,
    parse_delivery_command_v2,
    parse_delivery_receipt_v2,
    serialize_contract_json_v2,
)
from app.models.aqub_delivery_v2 import AQUBV2DeliveryLedger, AQUBV2RouteAuthority

JsonObject = dict[str, Any]
Provider = Callable[[JsonObject], Awaitable[str]]
DEFAULT_STALE_AFTER = timedelta(seconds=30)


class AQUBV2DeliveryMode(StrEnum):
    DISABLED = "disabled"
    SYNTHETIC_REHEARSAL = "synthetic_rehearsal"


class AQUBV2DeliveryExecutorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AQUBV2OutcomeUnknown(RuntimeError):
    """Provider boundary was crossed but no terminal evidence was obtained."""


def _now_iso(value: datetime) -> str:
    observed = value.astimezone(UTC)
    return observed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _opaque(kind: str, value: str) -> str:
    return f"{kind}:a{hashlib.sha256(value.encode()).hexdigest()}"


def _ack(
    command: Mapping[str, Any],
    *,
    status: str,
    ack_ref: str,
    now: datetime,
    reason: str | None = None,
) -> JsonObject:
    value: JsonObject = {
        "schema_version": "aqub.delivery-ack.v2",
        "ack_ref": ack_ref,
        "command_id": command["command_id"],
        "idempotency_key": command["idempotency_key"],
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
        "observed_at": _now_iso(now),
        "status": status,
    }
    if reason is not None:
        value["reason_code"] = reason
    parse_delivery_ack_v2(value)
    return value


def _receipt(
    command: Mapping[str, Any],
    *,
    status: str,
    now: datetime,
    delivered_message_ref: str | None = None,
    failure_code: str | None = None,
) -> JsonObject:
    token = f"{command['command_id']}:{status}"
    value: JsonObject = {
        "schema_version": "aqub.delivery-receipt.v2",
        "receipt_ref": _opaque("receipt", token),
        "command_id": command["command_id"],
        "idempotency_key": command["idempotency_key"],
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
        "observed_at": _now_iso(now),
        "status": status,
        "retry_disposition": "reconcile_only" if status == "unknown" else "do_not_retry",
        "transport_receipt_ref": _opaque("receipt", f"transport:{token}"),
    }
    if status == "sent":
        value["delivered_message_ref"] = delivered_message_ref
    elif status == "failed":
        value["failure_code"] = failure_code or "PROVIDER_REJECTED"
    else:
        value["reconciliation_ref"] = _opaque("reconcile", token)
    parse_delivery_receipt_v2(value)
    return value


class AQUBV2DeliveryExecutor:
    def __init__(
        self,
        *,
        session: AsyncSession,
        provider: Provider,
        mode: AQUBV2DeliveryMode = AQUBV2DeliveryMode.DISABLED,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(mode) is not AQUBV2DeliveryMode:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_DELIVERY_MODE_INVALID")
        self._session = session
        self._provider = provider
        self._mode = mode
        self._clock = clock

    def _require_enabled(self) -> None:
        if self._mode is not AQUBV2DeliveryMode.SYNTHETIC_REHEARSAL:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_DELIVERY_DISABLED")

    async def accept(self, command_value: Mapping[str, Any], route: AQUBV2Route) -> JsonObject:
        self._require_enabled()
        command_doc = parse_delivery_command_v2(command_value)
        assert_route_match_v2(command_doc, route)
        command = command_doc.to_wire()
        await self._assert_current_route(command)
        command_canonical = serialize_contract_json_v2(command_doc)
        command_sha = hashlib.sha256(command_canonical).hexdigest()
        row_id = uuid4()
        ack_ref = _opaque("ack", str(row_id))
        statement = (
            insert(AQUBV2DeliveryLedger)
            .values(
                id=row_id,
                command_id=UUID(command["command_id"]),
                installation_ref=command["installation_ref"],
                conversation_ref=command["conversation_ref"],
                cube_ref=command["cube_ref"],
                route_generation=command["route_generation"],
                idempotency_key=command["idempotency_key"],
                command_sha256=command_sha,
                command_canonical=command_canonical,
                command_json=command,
                state="committed",
                ack_ref=ack_ref,
            )
            .on_conflict_do_nothing(
                index_elements=["installation_ref", "idempotency_key"]
            )
        )
        inserted = await self._session.scalar(statement.returning(AQUBV2DeliveryLedger.id))
        await self._session.commit()
        row = await self._load(command["installation_ref"], command["idempotency_key"])
        if row is None:
            raise RuntimeError("AQUB v2 delivery reservation missing")
        now = self._clock()
        if row.content_redacted_at is not None:
            rejected = _ack(
                command,
                status="rejected",
                ack_ref=_opaque(
                    "ack",
                    f"redacted:{command['installation_ref']}:{command['idempotency_key']}",
                ),
                now=now,
                reason="AQUB_V2_CONTENT_REDACTED",
            )
            return {"ack": rejected, "receipt": None}
        if row.command_sha256 != command_sha or str(row.command_id) != command["command_id"]:
            rejected = _ack(
                command,
                status="rejected",
                ack_ref=_opaque(
                    "ack",
                    f"rejected:{command['installation_ref']}:{command['idempotency_key']}",
                ),
                now=now,
                reason="AQUB_V2_IDEMPOTENCY_CONFLICT",
            )
            return {"ack": rejected, "receipt": None}
        if row.ack_ref is None:
            raise RuntimeError("AQUB v2 unredacted reservation missing acknowledgement")
        ack = _ack(
            command,
            status="committed" if inserted is not None else "duplicate",
            ack_ref=row.ack_ref,
            now=now,
        )
        return {"ack": ack, "receipt": row.receipt_json}

    async def dispatch_committed(self, command_id: UUID) -> JsonObject:
        self._require_enabled()
        row = await self._session.scalar(
            select(AQUBV2DeliveryLedger)
            .where(AQUBV2DeliveryLedger.command_id == command_id)
            .with_for_update()
        )
        if row is None:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_COMMAND_NOT_FOUND")
        if row.state != "committed":
            return {"kind": "not_dispatchable", "state": row.state, "receipt": row.receipt_json}
        if row.command_json is None:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_CONTENT_REDACTED")
        command = dict(row.command_json)
        try:
            await self._assert_current_route(command)
        except AQUBV2DeliveryExecutorError as exc:
            now = self._clock()
            receipt = _receipt(
                command,
                status="failed",
                now=now,
                failure_code=exc.code,
            )
            row.state = "rejected"
            row.receipt_json = receipt
            row.settled_at = now
            row.updated_at = now
            await self._session.commit()
            return {"kind": "rejected", "receipt": receipt}
        now = self._clock()
        row.state = "sending"
        row.send_started_at = now
        row.updated_at = now
        await self._session.commit()
        try:
            delivered_message_ref = await self._provider(command)
            receipt = _receipt(
                command,
                status="sent",
                now=self._clock(),
                delivered_message_ref=delivered_message_ref,
            )
            state = "sent"
        except AQUBV2OutcomeUnknown:
            receipt = _receipt(command, status="unknown", now=self._clock())
            state = "unknown"
        except Exception:
            receipt = _receipt(command, status="failed", now=self._clock())
            state = "failed"
        settled_id = await self._session.scalar(
            update(AQUBV2DeliveryLedger)
            .where(
                AQUBV2DeliveryLedger.id == row.id,
                AQUBV2DeliveryLedger.state == "sending",
            )
            .values(
                state=state,
                receipt_json=receipt,
                settled_at=self._clock(),
                updated_at=self._clock(),
            )
            .returning(AQUBV2DeliveryLedger.id)
        )
        await self._session.commit()
        if settled_id is None:
            current = await self._session.scalar(
                select(AQUBV2DeliveryLedger).where(AQUBV2DeliveryLedger.id == row.id)
            )
            return {
                "kind": "not_settled",
                "state": current.state if current is not None else "missing",
                "receipt": current.receipt_json if current is not None else None,
            }
        return {"kind": state, "receipt": receipt}

    async def cancel_for_revoked_consent(
        self,
        cancellation_value: Mapping[str, Any],
    ) -> JsonObject:
        """Fence a committed ACK using a separately authenticated revoke envelope."""

        self._require_enabled()
        try:
            cancellation = parse_aqub_v2_consent_cancellation(cancellation_value)
        except AQUBV2ConsentCancellationError as exc:
            raise AQUBV2DeliveryExecutorError(exc.code) from exc
        try:
            result = await self._session.execute(
                text(
                    """
                    SELECT result_status,receipt_json,cancellation_receipt_json
                      FROM cancel_aqub_v2_delivery_for_revoked_consent(
                        CAST(:command_id AS uuid),:command_sha256,
                        :revoked_consent_ref,:revoked_authority_epoch,
                        CAST(:cancellation_id AS uuid),:cancellation_ref,
                        :observed_at
                      )
                    """
                ),
                cancellation.sql_parameters(),
            )
            row = dict(result.mappings().one())
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise AQUBV2DeliveryExecutorError(
                "AQUB_V2_CONSENT_CANCELLATION_REFUSED"
            ) from exc
        return {
            "kind": str(row["result_status"]),
            "receipt": row["receipt_json"],
            "cancellation_receipt": row["cancellation_receipt_json"],
        }

    async def recover_stale_sending(
        self,
        command_id: UUID,
        *,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> JsonObject:
        self._require_enabled()
        row = await self._session.scalar(
            select(AQUBV2DeliveryLedger)
            .where(AQUBV2DeliveryLedger.command_id == command_id)
            .with_for_update()
        )
        if row is None or row.state != "sending" or row.send_started_at is None:
            return {"kind": "unchanged"}
        started = (
            row.send_started_at
            if row.send_started_at.tzinfo
            else row.send_started_at.replace(tzinfo=UTC)
        )
        if self._clock() - started < stale_after:
            return {"kind": "in_progress"}
        if row.command_json is None:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_CONTENT_REDACTED")
        receipt = _receipt(dict(row.command_json), status="unknown", now=self._clock())
        row.state = "unknown"
        row.receipt_json = receipt
        row.settled_at = self._clock()
        row.updated_at = self._clock()
        await self._session.commit()
        return {"kind": "unknown", "receipt": receipt}

    async def reconcile(self, command_id: UUID, terminal_receipt: Mapping[str, Any]) -> JsonObject:
        self._require_enabled()
        receipt_doc = parse_delivery_receipt_v2(terminal_receipt)
        receipt = receipt_doc.to_wire()
        if receipt["status"] not in {"sent", "failed"}:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_RECONCILIATION_TERMINAL_REQUIRED")
        row = await self._session.scalar(
            select(AQUBV2DeliveryLedger)
            .where(AQUBV2DeliveryLedger.command_id == command_id)
            .with_for_update()
        )
        if row is None or row.state != "unknown":
            raise AQUBV2DeliveryExecutorError("AQUB_V2_UNKNOWN_REQUIRED")
        if row.command_json is None:
            raise AQUBV2DeliveryExecutorError("AQUB_V2_CONTENT_REDACTED")
        command_doc = parse_delivery_command_v2(row.command_json)
        assert_delivery_correlation_v2(command_doc, receipt_doc)
        row.state = receipt["status"]
        row.receipt_json = receipt
        row.settled_at = self._clock()
        row.updated_at = self._clock()
        await self._session.commit()
        return {"kind": "reconciled", "receipt": receipt}

    async def _load(
        self, installation_ref: str, idempotency_key: str
    ) -> AQUBV2DeliveryLedger | None:
        value = await self._session.scalar(
            select(AQUBV2DeliveryLedger).where(
                AQUBV2DeliveryLedger.installation_ref == installation_ref,
                AQUBV2DeliveryLedger.idempotency_key == idempotency_key,
            )
        )
        return value if isinstance(value, AQUBV2DeliveryLedger) else None

    async def _assert_current_route(self, command: Mapping[str, Any]) -> None:
        route = await self._session.scalar(
            select(AQUBV2RouteAuthority)
            .where(
                AQUBV2RouteAuthority.installation_ref == command["installation_ref"],
                AQUBV2RouteAuthority.conversation_ref == command["conversation_ref"],
            )
            .with_for_update(read=True)
        )
        if (
            not isinstance(route, AQUBV2RouteAuthority)
            or route.state != "active"
            or route.cube_ref != command["cube_ref"]
            or route.route_generation != command["route_generation"]
        ):
            raise AQUBV2DeliveryExecutorError("AQUB_V2_ROUTE_NOT_ACTIVE")
