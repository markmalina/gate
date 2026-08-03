from __future__ import annotations

import httpx
import pytest

from app.application.become_i4_chain import I4_CONTRACT_DIGEST
from app.infrastructure.become_i4_connectivity import (
    MAYAKI_HEALTH_ENDPOINT,
    MAYAKI_PRIVATE_HOSTNAME,
    MAYAKI_PRIVATE_PORT,
    MAYAKI_SERVICE_NAME,
    MAYAKI_SOURCE_SHA,
    probe_mayaki_i4_connectivity,
)


class FakeHealthClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def get(self, url: str, *, timeout: float) -> httpx.Response:
        self.calls += 1
        assert url == MAYAKI_HEALTH_ENDPOINT
        assert timeout == 5.0
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _health(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "become.i4-service-health@1",
        "service": "mayaki",
        "source_sha": MAYAKI_SOURCE_SHA,
        "contract_digest": I4_CONTRACT_DIGEST,
        "internal_only": True,
        "external_effects": 0,
    }
    value.update(overrides)
    return value


@pytest.mark.anyio
async def test_content_free_health_proves_exact_private_target() -> None:
    client = FakeHealthClient(
        response=httpx.Response(
            200,
            json=_health(),
            request=httpx.Request("GET", MAYAKI_HEALTH_ENDPOINT),
        )
    )
    proof = await probe_mayaki_i4_connectivity(client)
    assert proof == {
        "mayaki_connectivity_proven": True,
        "mayaki_connectivity_error_code": None,
        "mayaki_service_name": MAYAKI_SERVICE_NAME,
        "mayaki_private_hostname": MAYAKI_PRIVATE_HOSTNAME,
        "mayaki_private_port": MAYAKI_PRIVATE_PORT,
        "mayaki_source_sha": MAYAKI_SOURCE_SHA,
        "mayaki_health_external_effects": 0,
    }
    assert client.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        {"source_sha": "f" * 40},
        {"external_effects": 1},
        {"unexpected": True},
    ],
)
async def test_mutated_health_fails_closed(mutation: dict[str, object]) -> None:
    client = FakeHealthClient(
        response=httpx.Response(
            200,
            json=_health(**mutation),
            request=httpx.Request("GET", MAYAKI_HEALTH_ENDPOINT),
        )
    )
    proof = await probe_mayaki_i4_connectivity(client)
    assert proof["mayaki_connectivity_proven"] is False
    assert proof["mayaki_connectivity_error_code"] == "health_receipt_invalid"
    assert client.calls == 1


@pytest.mark.anyio
async def test_connection_failure_is_content_free_and_never_posts() -> None:
    client = FakeHealthClient(
        error=httpx.ConnectError(
            "connect",
            request=httpx.Request("GET", MAYAKI_HEALTH_ENDPOINT),
        )
    )
    proof = await probe_mayaki_i4_connectivity(client)
    assert proof["mayaki_connectivity_proven"] is False
    assert proof["mayaki_connectivity_error_code"] == "connection_not_established"
    assert client.calls == 1
