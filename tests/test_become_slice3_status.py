from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure.become_slice3_status import (
    EXPECTED_ALEMBIC_HEAD,
    EXPECTED_RELATIONS_SOURCE_SHA,
    become_slice3_production_evidence,
    validate_relations_health,
)


def _relations_health(*, source_sha: str = EXPECTED_RELATIONS_SOURCE_SHA) -> dict[str, Any]:
    return {
        "schema": "become.i4-service-health@1",
        "service": "relations",
        "status": "ok",
        "source_sha": source_sha,
        "external_effects": 0,
        "provider_calls": 0,
        "slice3_learner_link": {
            "execution_count": 0,
            "external_effects": 0,
            "provider_calls": 0,
        },
    }


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "GATE_BECOME_PILOT_SLICE3_ENABLED": False,
        "GATE_BECOME_PILOT_SLICE3_DATA_GATE_APPROVED": False,
        "GATE_BECOME_PILOT_SLICE3_DELIVERY_ENABLED": False,
        "GATE_BECOME_PILOT_SLICE3_RELATIONS_ENABLED": False,
        "GATE_BECOME_PILOT_SLICE3_MAX_TELEGRAM_SENDS": 0,
        "GATE_BECOME_PILOT_SLICE3_MAX_RELATIONS_POSTS": 0,
        "GATE_BECOME_PILOT_SLICE3_RELATIONS_URL": (
            "http://relations.internal:3000/internal/become/slice3/learner-link"
        ),
    }
    values.update(overrides)
    return Settings(**values)


class _MappingResult:
    def __init__(self, row: dict[str, int]) -> None:
        self._row = row

    def mappings(self) -> _MappingResult:
        return self

    def one(self) -> dict[str, int]:
        return self._row


class _ReadOnlySession:
    def __init__(
        self,
        row: dict[str, int] | None = None,
        *,
        alembic_head: str | None = EXPECTED_ALEMBIC_HEAD,
    ) -> None:
        self.alembic_head = alembic_head
        self.row = row or {
            "candidate_total_count": 0,
            "candidate_count": 0,
            "slot_count": 0,
            "invitation_count": 0,
            "accepted_count": 0,
            "expiry_due_count": 0,
            "acceptance_count": 0,
            "delivery_count": 0,
            "delivery_reserved_count": 0,
            "delivery_unknown_count": 0,
            "outbox_count": 0,
            "outbox_reserved_count": 0,
            "relations_unknown_count": 0,
            "relations_executed_count": 0,
            "candidate_tombstone_count": 0,
        }
        self.statements: list[str] = []

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        yield

    async def scalar(self, statement: object) -> str | None:
        self.statements.append(str(statement))
        return self.alembic_head

    async def execute(
        self, statement: object, parameters: dict[str, Any]
    ) -> _MappingResult:
        self.statements.append(str(statement))
        assert set(parameters) == {"now"}
        return _MappingResult(self.row)


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if any(
                fragment in key.lower()
                for fragment in (
                    "telegram_actor",
                    "dialog_id",
                    "username",
                    "display_name",
                    "callback_ref",
                    "subject_ref",
                    "content",
                )
            ) and key not in {
                "private_content_exposed",
                "content_exposed",
            }:
                return True
            if _contains_sensitive_key(nested):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def test_relations_health_rejects_wrong_sha_without_substituting_zeroes() -> None:
    result = validate_relations_health(_relations_health(source_sha="b" * 40))

    assert result == {
        "ready": False,
        "error_code": "slice3_relations_health_source_sha_mismatch",
        "source_sha": None,
        "execution_count": None,
        "provider_calls": None,
        "external_effects": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "wrong"},
        {
            **_relations_health(),
            "slice3_learner_link": {
                "execution_count": 0,
                "external_effects": 1,
                "provider_calls": 0,
            },
        },
        {
            **_relations_health(),
            "slice3_learner_link": {
                "execution_count": True,
                "external_effects": 0,
                "provider_calls": 0,
            },
        },
    ],
)
def test_relations_health_fails_closed_on_schema_or_effects(payload: object) -> None:
    result = validate_relations_health(payload)

    assert result["ready"] is False
    assert result["execution_count"] is None
    assert result["external_effects"] is None
    assert result["provider_calls"] is None


@pytest.mark.anyio
async def test_default_off_evidence_is_observed_redacted_and_read_only() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_relations_health())

    session = _ReadOnlySession()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await become_slice3_production_evidence(
            session,  # type: ignore[arg-type]
            settings=_settings(),
            http_client=client,
            now=datetime(2026, 8, 5, 18, 0, tzinfo=UTC),
        )

    assert result["ready"] is True
    assert result["error_code"] is None
    assert result["source_sha"] == "a" * 40
    assert result["alembic_head"] == EXPECTED_ALEMBIC_HEAD
    assert result["gates"] == {
        "runtime_enabled": False,
        "data_gate_approved": False,
        "delivery_enabled": False,
        "relations_enabled": False,
    }
    assert result["caps"] == {"telegram_sends": 0, "relations_posts": 0}
    assert result["counts"] is not None
    assert all(value == 0 for value in result["counts"].values())
    assert result["rollback_preflight"]["schema_downgrade_allowed"] is True
    assert result["relations"] == {
        "ready": True,
        "error_code": None,
        "source_sha": EXPECTED_RELATIONS_SOURCE_SHA,
        "execution_count": 0,
        "provider_calls": 0,
        "external_effects": 0,
    }
    assert result["mutation_allowed"] is False
    assert result["private_content_exposed"] is False
    assert result["provider_identifiers_exposed"] is False
    assert result["telegram_identifiers_exposed"] is False
    assert not _contains_sensitive_key(result)
    assert [request.method for request in requests] == ["GET"]
    assert requests[0].url.path == "/healthz"
    assert len(session.statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in session.statements)


@pytest.mark.anyio
async def test_newer_linear_migration_head_preserves_slice3_readiness() -> None:
    session = _ReadOnlySession(alembic_head="0093_beta_checkpoint_dispatch")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_relations_health())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await become_slice3_production_evidence(
            session,  # type: ignore[arg-type]
            settings=_settings(),
            http_client=client,
        )

    assert result["ready"] is True
    assert result["error_code"] is None
    assert result["expected_alembic_head"] == EXPECTED_ALEMBIC_HEAD
    assert result["alembic_head"] == "0093_beta_checkpoint_dispatch"


@pytest.mark.anyio
async def test_relations_unavailable_makes_status_explicitly_not_ready() -> None:
    session = _ReadOnlySession()
    async with httpx.AsyncClient() as client:
        result = await become_slice3_production_evidence(
            session,  # type: ignore[arg-type]
            settings=_settings(GATE_BECOME_PILOT_SLICE3_RELATIONS_URL=""),
            http_client=client,
        )

    assert result["ready"] is False
    assert result["error_code"] == "slice3_relations_health_not_configured"
    assert result["relations"]["execution_count"] is None


@pytest.mark.anyio
async def test_nonzero_local_state_blocks_default_off_readiness() -> None:
    session = _ReadOnlySession(
        {
            **_ReadOnlySession().row,
            "delivery_reserved_count": 1,
            "delivery_count": 1,
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_relations_health())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await become_slice3_production_evidence(
            session,  # type: ignore[arg-type]
            settings=_settings(),
            http_client=client,
        )

    assert result["ready"] is False
    assert result["error_code"] == "slice3_state_nonzero"
    assert result["rollback_preflight"]["application_rollback_allowed"] is False
