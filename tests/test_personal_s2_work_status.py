"""Provider-free S2 status bridge exposed only through the owner OAuth Work stage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api.personal_stage import S2_STATUS_SCHEMA, create_app
from app.personal_telegram import work_stage
from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.work_stage import StageSettings, read_s2_status
from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator

STATUS_URL = "https://gate-ru-enrollment-staging.up.railway.app/s2/work-status"
TOKEN = "S" * 43
STATUS = {
    "account": "RU",
    "configured": True,
    "authenticated": True,
    "custody_state": "prepared",
    "runtime_connected": True,
    "owner_lease_present": True,
    "lease_generation": 5,
    "last_successful_check": "2026-11-27T21:15:57+00:00",
    "state": "ready",
    "send_enabled": False,
    "create_group_enabled": False,
    "error_code": None,
    "observed_at": "2026-11-27T21:20:00+00:00",
    "source": "s2_command_store",
    "provider_calls": 0,
}


def settings(**updates) -> StageSettings:
    values = {
        "GATE_ENV": "test",
        "database_url": "postgresql+asyncpg://stage:test@db.invalid/gate",
        "master_key": Fernet.generate_key().decode(),
        "resource": "https://gate.example/mcp",
        "issuer": "https://issuer.example/",
        "jwks_url": "https://issuer.example/jwks",
        "client_id": "work-client",
        "workspace": "mark-personal",
        "owner": "mark",
        "binding_id": uuid4(),
        "grant_id": uuid4(),
        "grant_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "approval_ref": "s2-status-test",
        "s2_status_url": STATUS_URL,
        "s2_status_token": TOKEN,
    }
    values.update(updates)
    return StageSettings(**values)


@pytest.mark.anyio
async def test_bridge_uses_exact_bearer_and_rejects_non_redacted_shapes(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=STATUS)

    monkeypatch.setattr(
        work_stage.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    assert await read_s2_status(STATUS_URL, TOKEN) == STATUS
    assert requests[0].headers["authorization"] == "Bearer " + TOKEN

    bad = {**STATUS, "provider_calls": 1}
    monkeypatch.setattr(
        work_stage.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=bad)),
            **kwargs,
        ),
    )
    with pytest.raises(CommandRuntimeError, match="s2_status_invalid"):
        await read_s2_status(STATUS_URL, TOKEN)


@pytest.mark.anyio
async def test_work_catalog_declares_separate_s2_status_without_enabling_dispatch():
    app = create_app(settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
    ) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    catalog = response.json()["result"]["tools"]
    readiness = next(tool for tool in catalog if tool["name"].endswith("readiness"))
    accepted = readiness["outputSchema"]["oneOf"][0]
    assert "s2_status" in accepted["required"]
    assert accepted["properties"]["runtime_connected"] == {"const": False}
    Draft202012Validator.check_schema(S2_STATUS_SCHEMA)


@pytest.mark.parametrize(
    "updates",
    [
        {"s2_status_url": "", "s2_status_token": TOKEN},
        {"s2_status_url": STATUS_URL, "s2_status_token": "short"},
        {
            "s2_status_url": "https://elsewhere.invalid/s2/work-status",
            "s2_status_token": TOKEN,
        },
    ],
)
def test_stage_refuses_partial_or_foreign_status_bridge(updates):
    with pytest.raises(ValueError):
        settings(**updates)
