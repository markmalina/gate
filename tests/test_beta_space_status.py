"""Redacted operational status for the isolated Katya Beta Space."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.config import Settings
from app.infrastructure.beta_space import beta_katya_space_operational_status


class ScalarSession:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)

    async def scalar(self, _statement: object) -> object:
        return next(self.values)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_status_is_redacted_when_activation_configuration_is_missing() -> None:
    settings = Settings(
        _env_file=None,
        GATE_ENV="production",
        GATE_BETA_CONNECTOR_ENABLED=True,
        GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
    )

    result = await beta_katya_space_operational_status(
        ScalarSession([None]),  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
    )

    assert result["state"] == "configuration_incomplete"
    assert result["binding_state"] == "absent"
    assert result["event_count"] == 0
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
    assert "telegram_chat_id" not in result
    assert "subject_telegram_user_id" not in result


@pytest.mark.anyio
async def test_status_reports_ready_binding_without_private_values() -> None:
    settings = Settings(
        _env_file=None,
        GATE_ENV="production",
        GATE_BETA_CONNECTOR_ENABLED=True,
        GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
        GATE_BETA_KATYA_SPACE_ENABLED=True,
        GATE_BETA_KATYA_SPACE_READY=True,
        GATE_BETA_KATYA_DELIVERY_ENABLED=True,
        GATE_BETA_KATYA_CHAT_ID="-100123",
        GATE_BETA_KATYA_USER_ID="123",
        GATE_BETA_KATYA_CONSENT_SHA256="a" * 64,
    )
    binding = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000485"),
        state="ready",
    )
    session = ScalarSession(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            binding,
            0,
            0,
            0,
            0,
            0,
        ]
    )

    result = await beta_katya_space_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
    )

    assert result["state"] == "awaiting_consent"
    assert result["production_ready"] is True
    assert result["configured"] == {
        "chat_binding": True,
        "subject_binding": True,
        "consent_digest": True,
    }
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
