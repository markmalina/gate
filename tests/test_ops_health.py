"""Owner-only redacted worker operations endpoint tests."""

import json
from datetime import UTC, datetime

import pytest
from app.api.health_routes import operations_health
from app.application.worker_health import TELEGRAM_WORKER, WorkerHealthRegistry
from app.core.config import Settings
from fastapi import FastAPI, HTTPException, Request


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/admin/ops/health",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )


@pytest.mark.anyio
async def test_ops_health_requires_admin_bearer() -> None:
    settings = Settings(_env_file=None, ADMIN_BEARER_TOKEN="synthetic-admin-token")

    with pytest.raises(HTTPException) as raised:
        await operations_health(
            _request(FastAPI()),
            authorization=None,
            settings=settings,
        )

    assert raised.value.status_code == 403


@pytest.mark.anyio
async def test_ops_health_reports_redacted_degradation() -> None:
    registry = WorkerHealthRegistry()
    registry.register(TELEGRAM_WORKER, enabled=True)
    registry.record_failure(
        TELEGRAM_WORKER,
        error_code="telegram_worker_iteration_failed",
        observed_at=datetime.now(UTC),
    )
    app = FastAPI()
    app.state.worker_health_registry = registry
    settings = Settings(_env_file=None, ADMIN_BEARER_TOKEN="synthetic-admin-token")

    response = await operations_health(
        _request(app),
        authorization="Bearer synthetic-admin-token",
        settings=settings,
    )
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload == {
        "status": "degraded",
        "reason": None,
        "workers": [
            {
                "name": "telegram",
                "enabled": True,
                "state": "degraded",
                "consecutive_failures": 1,
                "heartbeat_age_seconds": payload["workers"][0]["heartbeat_age_seconds"],
                "last_error_code": "telegram_worker_iteration_failed",
                "last_failure_age_seconds": (
                    payload["workers"][0]["last_failure_age_seconds"]
                ),
            }
        ],
    }
    assert "synthetic-admin-token" not in response.body.decode()


@pytest.mark.anyio
async def test_ops_health_fails_closed_without_registry() -> None:
    response = await operations_health(
        _request(FastAPI()),
        authorization="Bearer synthetic-admin-token",
        settings=Settings(
            _env_file=None,
            ADMIN_BEARER_TOKEN="synthetic-admin-token",
        ),
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "degraded",
        "reason": "worker_registry_unavailable",
        "workers": [],
    }
