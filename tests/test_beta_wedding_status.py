"""Redacted operational status for the owner-only Wedding in Minsk Space."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.beta_wedding_binding import (
    beta_wedding_space_operational_status,
)


class ScalarSession:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)

    async def scalar(self, _statement: object) -> object:
        return next(self.values)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_wedding_status_is_redacted_while_awaiting_binding() -> None:
    settings = Settings(
        _env_file=None,
        GATE_ENV="production",
        GATE_BETA_CONNECTOR_ENABLED=True,
        GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
    )

    result = await beta_wedding_space_operational_status(
        ScalarSession([None]),  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
    )

    assert result["state"] == "awaiting_binding"
    assert result["binding_state"] == "absent"
    assert result["fact_count"] == 0
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
    assert "telegram_chat_id" not in result
    assert "subject_telegram_user_id" not in result


@pytest.mark.anyio
async def test_wedding_status_reports_active_without_private_values() -> None:
    settings = Settings(
        _env_file=None,
        GATE_ENV="production",
        GATE_BETA_CONNECTOR_ENABLED=True,
        GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
    )
    binding = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000496"),
        state="consented",
    )
    session = ScalarSession(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            binding,
            2,
            2,
            2,
            0,
        ]
    )

    result = await beta_wedding_space_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
    )

    assert result["state"] == "active"
    assert result["production_ready"] is True
    assert result["configured"] == {
        "chat_binding": True,
        "subject_binding": True,
        "activation_digest": True,
    }
    assert result["event_count"] == 2
    assert result["fact_count"] == 2
    assert result["reply_sent_count"] == 2
    assert result["reply_unknown_count"] == 0
    assert result["gates"]["delivery_enabled"] is True
