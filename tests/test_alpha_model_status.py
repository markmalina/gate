"""Query-contract tests for redacted Alpha model operational status."""

from datetime import UTC, datetime
from typing import Any

import pytest
from app.infrastructure.alpha_model_status import (
    alpha_model_operational_status,
)


class FakeScalarSession:
    def __init__(self, results: list[int]) -> None:
        self.results = results
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return self.results.pop(0)


@pytest.mark.anyio
async def test_status_uses_exact_staged_deployment_and_current_month_reservations() -> None:
    session = FakeScalarSession([1, 4, 2, 1, 2_048, 256, 1, 0])

    status = await alpha_model_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    assert status.staged_deployments == 1
    assert status.model_turns == 4
    assert status.proposed_turns == 2
    assert status.failed_turns == 1
    assert status.reserved_tokens == 2_304
    assert status.reserved_microusd == 13_824
    assert status.remaining_microusd == 49_986_176
    assert status.provider_preflight_successes == 1
    assert status.provider_preflight_failures == 0
    assert len(session.statements) == 8
    assert "agent_deployments.runtime_profile" in str(session.statements[0])
    assert "agent_deployments.model_profile" in str(session.statements[0])
    assert "alpha_usage_records.created_at" in str(session.statements[-1])
    assert "alpha_usage_records.state" in str(session.statements[-1])
