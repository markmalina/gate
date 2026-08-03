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
    PRODUCTION_HEALTH_TARGETS,
    BecomeI4HealthTarget,
    probe_all_i4_connectivity,
    probe_mayaki_i4_connectivity,
)


class FakeHealthClient:
    def __init__(
        self,
        *,
        mutations: dict[str, dict[str, object]] | None = None,
        failing_endpoint: str | None = None,
    ) -> None:
        self.mutations = mutations or {}
        self.failing_endpoint = failing_endpoint
        self.methods: list[tuple[str, str]] = []

    async def get(self, url: str, *, timeout: float) -> httpx.Response:
        self.methods.append(("GET", url))
        assert timeout == 5.0
        target = next(item for item in PRODUCTION_HEALTH_TARGETS if item.endpoint == url)
        if url == self.failing_endpoint:
            raise httpx.ConnectError("connect", request=httpx.Request("GET", url))
        return httpx.Response(
            200,
            json=_health(target, **self.mutations.get(target.stage, {})),
            request=httpx.Request("GET", url),
        )


def _health(
    target: BecomeI4HealthTarget,
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "become.i4-service-health@1",
        "service": target.stage,
        "source_sha": target.source_sha,
        "contract_digest": I4_CONTRACT_DIGEST,
        "internal_only": True,
        "external_effects": 0,
    }
    value.update(overrides)
    return value


@pytest.mark.anyio
async def test_content_free_health_proves_all_exact_private_targets() -> None:
    client = FakeHealthClient()
    proof = await probe_all_i4_connectivity(client)
    assert {\n        key\n        for key, value in proof.items()\n        if key.endswith("_connectivity_proven") and value\n    } == {
        "mayaki_connectivity_proven",
        "oko_connectivity_proven",
        "take_connectivity_proven",
    }
    assert client.methods == [("GET", target.endpoint) for target in PRODUCTION_HEALTH_TARGETS]
    assert all(method == "GET" for method, _ in client.methods)
    for target in PRODUCTION_HEALTH_TARGETS:
        assert proof[f"{target.stage}_service_name"] == target.service_name
        assert proof[f"{target.stage}_private_hostname"] == target.private_hostname
        assert proof[f"{target.stage}_private_port"] == target.private_port
        assert proof[f"{target.stage}_source_sha"] == target.source_sha
        assert proof[f"{target.stage}_health_external_effects"] == 0


@pytest.mark.anyio
async def test_backward_compatible_mayaki_proof() -> None:
    client = FakeHealthClient()
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
    assert client.methods == [("GET", MAYAKI_HEALTH_ENDPOINT)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        {"source_sha": "f" * 40},
        {"external_effects": 1},
        {"internal_only": False},
        {"unexpected": True},
    ],
)
async def test_mutated_health_fails_closed(mutation: dict[str, object]) -> None:
    client = FakeHealthClient(mutations={"oko": mutation})
    proof = await probe_all_i4_connectivity(client)
    assert proof["oko_connectivity_proven"] is False
    assert proof["oko_connectivity_error_code"] == "health_receipt_invalid"
    assert all(method == "GET" for method, _ in client.methods)


@pytest.mark.anyio
async def test_connection_failure_is_content_free_and_never_posts() -> None:
    failing = PRODUCTION_HEALTH_TARGETS[-1]
    client = FakeHealthClient(failing_endpoint=failing.endpoint)
    proof = await probe_all_i4_connectivity(client)
    assert proof["take_connectivity_proven"] is False
    assert proof["take_connectivity_error_code"] == "connection_not_established"
    assert all(method == "GET" for method, _ in client.methods)
