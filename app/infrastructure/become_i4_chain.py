"""One-attempt private transport for the provider-disabled BECOME I4 chain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx

from app.application.become_i4_chain import (
    BecomeI4Stage,
    JsonObject,
    canonical_digest,
    validate_i4_receipt,
)

RELATIONS_SOURCE_SHA = "a9bc98f8ec81ddd767381f414799f19e159701a4"
MAYAKI_SOURCE_SHA = "2078d230852af1c7e3a309f00d716fa2fc664f68"
OKO_SOURCE_SHA = "2c304529d04be43726e677d2b864c4b5f8283be8"
TAKE_SOURCE_SHA = "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"

RELATIONS_ENDPOINT = (
    "http://become-relations-controlled.railway.internal:8080/internal/become/i4"
)
MAYAKI_ENDPOINT = (
    "http://become-mayaki-controlled.railway.internal:3000/internal/become/i4"
)
OKO_ENDPOINT = "http://become-oko-controlled.railway.internal:3000/internal/become/i4"
TAKE_ENDPOINT = "http://become-take-controlled.railway.internal:8000/internal/become/i4"

_EXACT_PRODUCTION = {
    BecomeI4Stage.RELATIONS: (RELATIONS_ENDPOINT, RELATIONS_SOURCE_SHA),
    BecomeI4Stage.MAYAKI: (MAYAKI_ENDPOINT, MAYAKI_SOURCE_SHA),
    BecomeI4Stage.OKO: (OKO_ENDPOINT, OKO_SOURCE_SHA),
    BecomeI4Stage.TAKE: (TAKE_ENDPOINT, TAKE_SOURCE_SHA),
}


class BecomeI4TransportState(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True, slots=True)
class BecomeI4ServiceTarget:
    stage: BecomeI4Stage
    endpoint: str
    source_sha: str

    def __post_init__(self) -> None:
        expected_endpoint, expected_sha = _EXACT_PRODUCTION[self.stage]
        if self.endpoint != expected_endpoint or self.source_sha != expected_sha:
            raise ValueError(f"exact_production_target_required:{self.stage.value}")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or not parsed.hostname.endswith(".railway.internal")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/internal/become/i4"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"private_service_endpoint_required:{self.stage.value}")


@dataclass(frozen=True, slots=True)
class BecomeI4ProductionTargets:
    relations: BecomeI4ServiceTarget = BecomeI4ServiceTarget(
        BecomeI4Stage.RELATIONS, RELATIONS_ENDPOINT, RELATIONS_SOURCE_SHA
    )
    mayaki: BecomeI4ServiceTarget = BecomeI4ServiceTarget(
        BecomeI4Stage.MAYAKI, MAYAKI_ENDPOINT, MAYAKI_SOURCE_SHA
    )
    oko: BecomeI4ServiceTarget = BecomeI4ServiceTarget(
        BecomeI4Stage.OKO, OKO_ENDPOINT, OKO_SOURCE_SHA
    )
    take: BecomeI4ServiceTarget = BecomeI4ServiceTarget(
        BecomeI4Stage.TAKE, TAKE_ENDPOINT, TAKE_SOURCE_SHA
    )

    def for_stage(self, stage: BecomeI4Stage) -> BecomeI4ServiceTarget:
        return {
            BecomeI4Stage.RELATIONS: self.relations,
            BecomeI4Stage.MAYAKI: self.mayaki,
            BecomeI4Stage.OKO: self.oko,
            BecomeI4Stage.TAKE: self.take,
        }[stage]


@dataclass(frozen=True, slots=True)
class ReservedBecomeI4Stage:
    stage: BecomeI4Stage
    request_digest: str
    request_payload: JsonObject
    duplicate: bool


@dataclass(frozen=True, slots=True)
class BecomeI4TransportOutcome:
    stage: BecomeI4Stage
    state: BecomeI4TransportState
    request_digest: str
    receipt_payload: JsonObject | None
    attempted: bool
    reconciliation_required: bool


class BecomeI4HttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: object,
        timeout: float,
    ) -> httpx.Response: ...


class PostReservedBecomeI4StageOnce:
    """Issue at most one POST and never resend a duplicate reservation."""

    def __init__(self, client: BecomeI4HttpClient, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("bounded_i4_timeout_required")
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        *,
        target: BecomeI4ServiceTarget,
        reservation: ReservedBecomeI4Stage,
    ) -> BecomeI4TransportOutcome:
        if reservation.stage is not target.stage:
            raise ValueError("reservation_target_stage_mismatch")
        if canonical_digest(reservation.request_payload) != reservation.request_digest:
            raise ValueError("reserved_request_digest_mismatch")
        if reservation.duplicate:
            return BecomeI4TransportOutcome(
                stage=reservation.stage,
                state=BecomeI4TransportState.RECONCILIATION_REQUIRED,
                request_digest=reservation.request_digest,
                receipt_payload=None,
                attempted=False,
                reconciliation_required=True,
            )

        try:
            response = await self._client.post(
                target.endpoint,
                json=reservation.request_payload,
                timeout=self._timeout_seconds,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return _outcome(
                reservation,
                state=BecomeI4TransportState.FAILED,
                error_code="connection_not_established",
            )
        except (
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
            httpx.TransportError,
        ):
            return _outcome(
                reservation,
                state=BecomeI4TransportState.UNKNOWN,
                error_code="transport_outcome_ambiguous",
            )

        if 300 <= response.status_code < 500:
            return _outcome(
                reservation,
                state=BecomeI4TransportState.FAILED,
                error_code=f"service_http_{response.status_code}",
            )
        if response.status_code != 200:
            return _outcome(
                reservation,
                state=BecomeI4TransportState.UNKNOWN,
                error_code=f"service_http_{response.status_code}_ambiguous",
            )
        try:
            raw_receipt = cast(object, response.json())
            receipt = validate_i4_receipt(
                stage=reservation.stage,
                receipt=raw_receipt,
                request=reservation.request_payload,
                expected_source_sha=target.source_sha,
            )
        except (ValueError, TypeError):
            return _outcome(
                reservation,
                state=BecomeI4TransportState.UNKNOWN,
                error_code="service_receipt_invalid",
            )
        return BecomeI4TransportOutcome(
            stage=reservation.stage,
            state=BecomeI4TransportState.COMPLETED,
            request_digest=reservation.request_digest,
            receipt_payload=receipt,
            attempted=True,
            reconciliation_required=False,
        )


def _outcome(
    reservation: ReservedBecomeI4Stage,
    *,
    state: BecomeI4TransportState,
    error_code: str,
) -> BecomeI4TransportOutcome:
    payload: JsonObject = {
        "schema": "gate.become-i4-stage-outcome@1",
        "stage": reservation.stage.value,
        "request_digest": reservation.request_digest,
        "classification": state.value,
        "error_code": error_code,
        "external_effects": 0,
    }
    return BecomeI4TransportOutcome(
        stage=reservation.stage,
        state=state,
        request_digest=reservation.request_digest,
        receipt_payload=payload,
        attempted=True,
        reconciliation_required=state is BecomeI4TransportState.UNKNOWN,
    )
