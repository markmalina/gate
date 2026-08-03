"""Content-free private MAYAKI connectivity proof for the BECOME I4 boundary."""

from __future__ import annotations

from typing import Protocol, cast

import httpx

from app.application.become_i4_chain import I4_CONTRACT_DIGEST, JsonObject

MAYAKI_SERVICE_NAME = "become-mayaki-controlled"
MAYAKI_PRIVATE_HOSTNAME = f"{MAYAKI_SERVICE_NAME}.railway.internal"
MAYAKI_PRIVATE_PORT = 3000
MAYAKI_SOURCE_SHA = "2078d230852af1c7e3a309f00d716fa2fc664f68"
MAYAKI_HEALTH_ENDPOINT = (
    f"http://{MAYAKI_PRIVATE_HOSTNAME}:{MAYAKI_PRIVATE_PORT}/health"
)
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


async def probe_mayaki_i4_connectivity(
    client: BecomeI4HealthClient,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """GET the provider-disabled health boundary without touching a chain slot."""

    if timeout_seconds <= 0 or timeout_seconds > 10:
        raise ValueError("bounded_i4_health_timeout_required")
    proof = _proof(proven=False, error_code="connection_not_established")
    try:
        response = await client.get(
            MAYAKI_HEALTH_ENDPOINT,
            timeout=timeout_seconds,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return proof
    except httpx.TransportError:
        return _proof(proven=False, error_code="health_transport_error")
    if response.status_code != 200:
        return _proof(
            proven=False,
            error_code=f"health_http_{response.status_code}",
        )
    try:
        raw = cast(object, response.json())
        _validate_health(raw)
    except (TypeError, ValueError):
        return _proof(proven=False, error_code="health_receipt_invalid")
    return _proof(proven=True, error_code=None)


def _validate_health(raw: object) -> JsonObject:
    if not isinstance(raw, dict) or frozenset(raw) != _HEALTH_FIELDS:
        raise ValueError("health_receipt_fields_invalid")
    expected: JsonObject = {
        "schema": "become.i4-service-health@1",
        "service": "mayaki",
        "source_sha": MAYAKI_SOURCE_SHA,
        "contract_digest": I4_CONTRACT_DIGEST,
        "internal_only": True,
        "external_effects": 0,
    }
    if raw != expected:
        raise ValueError("health_receipt_binding_invalid")
    return cast(JsonObject, raw)


def _proof(*, proven: bool, error_code: str | None) -> dict[str, object]:
    return {
        "mayaki_connectivity_proven": proven,
        "mayaki_connectivity_error_code": error_code,
        "mayaki_service_name": MAYAKI_SERVICE_NAME,
        "mayaki_private_hostname": MAYAKI_PRIVATE_HOSTNAME,
        "mayaki_private_port": MAYAKI_PRIVATE_PORT,
        "mayaki_source_sha": MAYAKI_SOURCE_SHA,
        "mayaki_health_external_effects": 0,
    }
