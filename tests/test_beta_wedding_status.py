"""Redacted operational status for the owner-only Wedding in Minsk Space."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.beta_wedding_binding import (
    beta_wedding_space_operational_status,
)


class RowResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self.rows


class ScalarSession:
    def __init__(
        self,
        values: list[object],
        *,
        row_groups: list[list[tuple[object, ...]]] | None = None,
    ) -> None:
        self.values = iter(values)
        self.row_groups = iter(row_groups or [])
        self.executed: list[object] = []

    async def scalar(self, _statement: object) -> object:
        return next(self.values)

    async def execute(self, statement: object) -> RowResult:
        self.executed.append(statement)
        return RowResult(next(self.row_groups))


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
    assert result["material_job_counts"] == []
    assert result["material_delivery_counts"] == []
    assert result["material_delivery_unknown_count"] == 0
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
        actor_policy="group_members",
        provenance_state="verified",
        provenance_evidence_kind="authenticated_control_plane",
        checkpoint_last_exported_sequence=1,
    )
    fixed_now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    session = ScalarSession(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            binding,
            2,
            2,
            2,
            0,
            4,
            2,
            1,
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=2),
            4,
            2,
            1,
            1,
        ],
        row_groups=[
            [
                ("document", "structured", 1),
                ("voice", "failed", 1),
            ],
            [
                ("receipt", "sent", 2),
                ("structured_result", "sent", 1),
                ("structured_result", "unknown", 1),
            ],
            [
                ("telegram_timeout", 2),
                ("schema_invalid", 1),
            ],
            [("github_rate_limit", 3)],
        ],
    )

    result = await beta_wedding_space_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
        now=fixed_now,
    )

    assert result["state"] == "active"
    assert result["production_ready"] is True
    assert result["configured"] == {
        "chat_binding": True,
        "subject_binding": True,
        "activation_digest": True,
        "actor_policy_explicit": True,
        "provenance_recorded": True,
    }
    assert result["actor_policy"] == "group_members"
    assert result["provenance_state"] == "verified"
    assert result["provenance_evidence"] == "authenticated_control_plane"
    assert result["event_count"] == 2
    assert result["fact_count"] == 2
    assert result["reply_sent_count"] == 2
    assert result["reply_unknown_count"] == 0
    assert result["material_job_counts"] == [
        {"media_kind": "document", "state": "structured", "count": 1},
        {"media_kind": "voice", "state": "failed", "count": 1},
    ]
    assert result["material_delivery_counts"] == [
        {"phase": "receipt", "state": "sent", "count": 2},
        {"phase": "structured_result", "state": "sent", "count": 1},
        {"phase": "structured_result", "state": "unknown", "count": 1},
    ]
    assert result["material_delivery_unknown_count"] == 1
    assert result["queue_depth"] == 3
    assert result["checkpoint_lag"] == 3
    assert result["oldest_nonterminal_age_seconds"] == 300
    assert result["attempts"] == 6
    assert result["stale_recovery_count"] == 2
    assert result["terminal_error_categories"] == {
        "external_transient": 5,
        "validation": 1,
    }
    assert result["gates"]["delivery_enabled"] is True

    statements = [str(statement) for statement in session.executed]
    assert len(statements) == 4
    assert "beta_material_jobs.workspace_id" in statements[0]
    assert "beta_material_jobs.binding_id" in statements[0]
    assert "beta_material_jobs.workspace_id" in statements[1]
    assert "beta_material_jobs.binding_id" in statements[1]
    assert "JOIN beta_material_jobs" in statements[1]
    assert "beta_space_work_items.workspace_id" in statements[2]
    assert "beta_space_work_items.binding_id" in statements[2]
    assert "beta_checkpoint_dispatch_intents.workspace_id" in statements[3]
    assert "beta_checkpoint_dispatch_intents.binding_id" in statements[3]

    serialized = repr(result)
    for private_name in (
        "telegram_file_id",
        "file_name",
        "sender_label",
        "candidate_text",
        "candidate_sha256",
        "provider_message_id",
        "error_code",
        "telegram_timeout",
        "schema_invalid",
        "github_rate_limit",
        "job_id",
    ):
        assert private_name not in serialized


@pytest.mark.anyio
async def test_wedding_status_quarantines_legacy_mutable_provenance() -> None:
    settings = Settings(
        _env_file=None,
        GATE_ENV="production",
        GATE_BETA_CONNECTOR_ENABLED=True,
        GATE_BETA_PRIVACY_MODE_CONFIRMED=True,
    )
    binding = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000496"),
        state="quarantined",
        actor_policy="group_members",
        provenance_state="quarantined",
        provenance_evidence_kind="legacy_mutable_telegram",
        checkpoint_last_exported_sequence=17,
    )
    session = ScalarSession(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            binding,
            17,
            17,
            7,
            0,
            17,
            0,
            0,
            None,
            None,
            0,
            0,
            0,
            0,
        ],
        row_groups=[[], [], [], []],
    )

    result = await beta_wedding_space_operational_status(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        settings=settings,
    )

    assert result["state"] == "quarantined"
    assert result["production_ready"] is False
    assert result["gates"]["space_enabled"] is False
    assert result["gates"]["delivery_enabled"] is False
    assert result["event_count"] == 17
    assert result["fact_count"] == 17
    assert result["private_identifiers_exposed"] is False
    assert result["private_payload_exposed"] is False
