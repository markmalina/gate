"""Liveness and deployment-readiness route tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from app.api.health_routes import router
from app.application.readiness import (
    CheckReadinessService,
    ReadinessFailureCode,
    ReadinessProbeError,
    ReadinessSnapshot,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Probe:
    def __init__(
        self,
        *,
        snapshot: ReadinessSnapshot | None = None,
        error: ReadinessProbeError | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error

    async def inspect(self) -> ReadinessSnapshot:
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def _service(
    *,
    expected: frozenset[str] = frozenset({"expected-secret-revision"}),
    current: frozenset[str] = frozenset({"expected-secret-revision"}),
    error: ReadinessProbeError | None = None,
) -> CheckReadinessService:
    return CheckReadinessService(
        _Probe(
            snapshot=ReadinessSnapshot(expected_heads=expected, current_heads=current),
            error=error,
        )
    )


def _app(service: CheckReadinessService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            app.state.readiness_service = service
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


@pytest.mark.anyio
async def test_live_does_not_require_database_state() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://test",
    ) as client:
        response = await client.get("/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "gate"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("service", "status_code", "payload"),
    [
        (
            _service(),
            200,
            {
                "status": "ready",
                "reason": None,
                "checks": {"database": "ok", "migrations": "ok"},
            },
        ),
        (
            _service(current=frozenset({"old-secret-revision"})),
            503,
            {
                "status": "not_ready",
                "reason": "migration_heads_mismatch",
                "checks": {"database": "ok", "migrations": "failed"},
            },
        ),
        (
            _service(
                error=ReadinessProbeError(ReadinessFailureCode.DATABASE_UNAVAILABLE)
            ),
            503,
            {
                "status": "not_ready",
                "reason": "database_unavailable",
                "checks": {"database": "failed", "migrations": "not_checked"},
            },
        ),
    ],
)
async def test_ready_exposes_only_redacted_contract(
    service: CheckReadinessService,
    status_code: int,
    payload: dict[str, object],
) -> None:
    app = _app(service)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/ready")

    assert response.status_code == status_code
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store"
    assert "expected-secret-revision" not in response.text
    assert "old-secret-revision" not in response.text
    assert "postgresql" not in response.text


@pytest.mark.anyio
async def test_ready_fails_closed_without_lifespan_service() -> None:
    app = _app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "check_failed",
        "checks": {"database": "unknown", "migrations": "unknown"},
    }
    assert response.headers["cache-control"] == "no-store"


def test_ready_openapi_declares_success_and_failure_schema() -> None:
    schema = _app().openapi()
    operation = schema["paths"]["/ready"]["get"]

    assert set(operation["responses"]) >= {"200", "503"}
    assert "ReadinessResponse" in schema["components"]["schemas"]
    for status_code in ("200", "503"):
        response_schema = operation["responses"][status_code]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": "#/components/schemas/ReadinessResponse"}
