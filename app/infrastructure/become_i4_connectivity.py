"""Content-free private connectivity proofs for the BECOME I4 boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

from app.application.become_i4_chain import I4_CONTRACT_DIGEST, JsonObject


@dataclass(frozen=True, slots=True)
class BecomeI4HealthTarget:
    stage: str
    service_name: str
    private_port: int
    health_path: str
    source_sha: str

    @property
    def private_hostname(self) -> str:
        return f"{self.service_name}.railway.internal"

    @property
    def endpoint(self) -> str:
        return (
            f"http://{self.private_hostname}:{self.private_port}"
            f"{self.health_path}"
        )


MAYAKI_TARGET = BecomeI4HealthTarget(
    stage="mayaki",
    service_name="become-mayaki-controlled",
    private_port=3000,
    health_path="/health",
    source_sha="2078d230852af1c7e3a309f00d716fa2fc664f68",
)
OKO_TARGET = BecomeI4HealthTarget(
    stage="oko",
    service_name="become-oko-controlled",
    private_port=3000,
    health_path="/healthz",
    source_sha="7b6e0eab766779e815ba8fc4c2c13352421ce38e",
)
TAKE_TARGET = BecomeI4HealthTarget(
    stage="take",
    service_name="become-take-controlled",
    private_port=8000,
    health_path="/healthz",
    source_sha="c88a592e161fd3dbe9af83acc719a825bacd0f03",
)
PRODUCTION_HEALTH_TARGETS = (MAYAKI_TARGET, OKO_TARGET, TAKE_TARGET)

MAYAKI_SERVICE_NAME = MAYAKI_TARGET.service_name
MAYAKI_PRIVATE_HOSTNAME = MAYAKI_TARGET.private_hostname
MAYAKI_PRIVATE_PORT = MAYAKI_TARGET.private_port
MAYAKI_SOURCE_SHA = MAYAKI_TARGET.source_sha
MAYAKI_HEALTH_ENDPOINT = MAYAKI_TARGET.endpoint

_HEALTH_FIELDS = frozenset(
    {
        "schema",
        "service",
        "source_sha",
        "contract_digest",
        "internal_only",
        "external_effects",
    }
)


class BecomeI4HealthClient(Protocol):
    async def get(self, url: str, *, timeout: float) -> httpx.Response: ...


async def probe_all_i4_connectivity(
    client: BecomeI4HealthClient,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """Run only bounded GETs against the three provider-disabled health endpoints."""

    proofs = await asyncio.gather(
        *(
            _probe_i4_connectivity(
                client,
                target=target,
                timeout_seconds=timeout_seconds,
            )
            for target in PRODUCTION_HEALTH_TARGETS
        )
    )
    return {key: value for proof in proofs for key, value in proof.items()}


async def probe_mayaki_i4_connectivity(
    client: BecomeI4HealthClient,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """Backward-compatible single MAYAKI GET proof."""

    return await _probe_i4_connectivity(
        client,
        target=MAYAKI_TARGET,
        timeout_seconds=timeout_seconds,
    )


async def _probe_i4_connectivity(
    client: BecomeI4HealthClient,
    *,
    target: BecomeI4HealthTarget,
    timeout_seconds: float,
) -> dict[str, object]:
    if timeout_seconds <= 0 or timeout_seconds > 10:
        raise ValueError("bounded_i4_health_timeout_required")
    proof = _proof(
        target=target,
        proven=False,
        error_code="connection_not_established",
    )
    try:
        response = await client.get(target.endpoint, timeout=timeout_seconds)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return proof
    except httpx.TransportError:
        return _proof(
            target=target,
            proven=False,
            error_code="health_transport_error",
        )
    if response.status_code != 200:
        return _proof(
            target=target,
            proven=False,
            error_code=f"health_http_{response.status_code}",
        )
    try:
        raw = cast(object, response.json())
        _validate_health(raw, target=target)
    except (TypeError, ValueError):
        return _proof(
            target=target,
            proven=False,
            error_code="health_receipt_invalid",
        )
    return _proof(target=target, proven=True, error_code=None)


def _validate_health(
    raw: object,
    *,
    target: BecomeI4HealthTarget,
) -> JsonObject:
    if not isinstance(raw, dict) or frozenset(raw) != _HEALTH_FIELDS:
        raise ValueError("health_receipt_fields_invalid")
    expected: JsonObject = {
        "schema": "become.i4-service-health@1",
        "service": target.stage,
        "source_sha": target.source_sha,
        "contract_digest": I4_CONTRACT_DIGEST,
        "internal_only": True,
        "external_effects": 0,
    }
    if raw != expected:
        raise ValueError("health_receipt_binding_invalid")
    return cast(JsonObject, raw)


def _proof(
    *,
    target: BecomeI4HealthTarget,
    proven: bool,
    error_code: str | None,
) -> dict[str, object]:
    prefix = target.stage
    return {
        f"{prefix}_connectivity_proven": proven,
        f"{prefix}_connectivity_error_code": error_code,
        f"{prefix}_service_name": target.service_name,
        f"{prefix}_private_hostname": target.private_hostname,
        f"{prefix}_private_port": target.private_port,
        f"{prefix}_source_sha": target.source_sha,
        f"{prefix}_health_external_effects": 0,
    }
