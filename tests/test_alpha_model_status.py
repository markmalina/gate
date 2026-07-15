"""Query-contract tests for redacted Alpha model operational status."""

from datetime import UTC, datetime
from typing import Any

import pytest
from app.infrastructure.alpha_model_status import (
    alpha_model_operational_status,
)


class FakeScalarSession:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return self.results.pop(0)


@pytest.mark.anyio
async def test_status_uses_exact_model_deployment_and_redacted_safety_signals() -> None:
    session = FakeScalarSession(
        [1, 4, 2, 1, 1, 0, 2_048, 256, 1, 0, 0, None, 0]
    )

    status = await alpha_model_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    assert status.staged_deployments == 1
    assert status.model_turns == 4
    assert status.proposed_turns == 2
    assert status.failed_turns == 1
    assert status.queued_turns == 1
    assert status.running_turns == 0
    assert status.model_delivery_grants == 0
    assert status.stuck_provider_preflights == 0
    assert status.last_provider_error_code is None
    assert status.reserved_tokens == 2_304
    assert status.reserved_microusd == 13_824
    assert status.remaining_microusd == 49_986_176
    assert status.provider_preflight_successes == 1
    assert status.provider_preflight_failures == 0
    assert len(session.statements) == 13
    assert "agent_deployments.runtime_profile" in str(session.statements[0])
    assert "agent_deployments.model_profile" in str(session.statements[0])
    assert "alpha_usage_records.created_at" in str(session.statements[6])
    assert "alpha_usage_records.state" in str(session.statements[6])
    assert "agent_installations" in str(session.statements[6])
    assert "agent_deployments.runtime_profile" in str(session.statements[6])
    assert "agent_deployments.model_profile" in str(session.statements[6])
    assert "alpha_model_preflights.created_at" in str(session.statements[7])
    assert "alpha_model_preflights.state" in str(session.statements[8])
    assert "alpha_model_preflights.updated_at" in str(session.statements[10])
    assert "alpha_model_preflights.error_code" in str(session.statements[11])
    assert "delivery_grants" in str(session.statements[12])
    assert "agent_deployments.runtime_profile" in str(session.statements[12])


@pytest.mark.anyio
async def test_status_keeps_only_bounded_provider_error_code_for_categorization() -> None:
    session = FakeScalarSession(
        [
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            2_048,
            0,
            1,
            1,
            "openai_rate_limited",
            0,
        ]
    )

    status = await alpha_model_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    assert status.stuck_provider_preflights == 1
    assert status.last_provider_error_code == "openai_rate_limited"
    assert status.model_delivery_grants == 0
