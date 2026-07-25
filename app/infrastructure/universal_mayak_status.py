"""Company-safe operational telemetry for the owner-only Universal Mayak.

Only configuration booleans, aggregate counts and bounded internal result codes
leave this module. No message, prompt, provider identifier, Telegram identifier,
person identity, secret or candidate text is selected.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

_AGENT_KEY = "oko"
_REVISION_KEY = "universal-mayak-v1"
_INSTALLATION_KEY = "universal-mayak-v1-owner-private"
_TERMINAL_ATTEMPT_STATES = frozenset({"sent", "failed", "unknown", "blocked"})
_INPUT_DIGEST_PREFIX = "mayak-input-sha256:"


async def universal_mayak_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    settings: Settings,
) -> dict[str, Any]:
    """Return one redacted snapshot scoped to the exact Oko agent definition."""

    workspace_rows = (
        await session.execute(
            sa.text(
                "SELECT id FROM workspaces "
                "WHERE key = :workspace_key AND is_active IS TRUE"
            ),
            {"workspace_key": workspace_key},
        )
    ).scalars().all()
    workspace_valid = len(workspace_rows) == 1
    workspace_id = workspace_rows[0] if workspace_valid else None

    installation = _zero_installation()
    sessions = _zero_states(("active", "paused", "completed", "handoff_required"))
    turns = _zero_states(("queued", "running", "proposed", "failed"))
    grants = _zero_states(("open", "used", "revoked"))
    attempts = _zero_states(
        ("reserved", "sending", "sent", "failed", "unknown", "blocked")
    )
    last_turn: dict[str, Any] | None = None
    last_attempt: dict[str, Any] | None = None

    if workspace_valid:
        parameters = {
            "workspace_id": workspace_id,
            "agent_key": _AGENT_KEY,
            "revision_key": _REVISION_KEY,
            "installation_key": _INSTALLATION_KEY,
        }
        installation = dict(
            (
                await session.execute(
                    sa.text(
                        "SELECT COUNT(*)::int AS total, "
                        "COUNT(*) FILTER (WHERE ai.state = 'discovered')::int "
                        " AS discovered, "
                        "COUNT(*) FILTER (WHERE ai.state = 'pending_approval')::int "
                        " AS pending_approval, "
                        "COUNT(*) FILTER (WHERE ai.state = 'active')::int AS active, "
                        "COUNT(*) FILTER (WHERE ai.state = 'paused')::int AS paused, "
                        "COUNT(*) FILTER (WHERE ai.state = 'revoked')::int AS revoked, "
                        "COUNT(*) FILTER (WHERE ai.state = 'active' "
                        " AND ad.state = 'active' "
                        " AND definition.agent_key = :agent_key "
                        " AND ir.revision_key = :revision_key "
                        " AND ir.validation_state = 'validated')::int AS exact_active "
                        "FROM agent_installations ai "
                        "LEFT JOIN agent_deployments ad ON ad.id = ai.deployment_id "
                        " AND ad.workspace_id = ai.workspace_id "
                        "LEFT JOIN agent_definitions definition "
                        " ON definition.id = ad.agent_definition_id "
                        " AND definition.workspace_id = ad.workspace_id "
                        "LEFT JOIN instruction_revisions ir "
                        " ON ir.id = ai.instruction_revision_id "
                        " AND ir.workspace_id = ai.workspace_id "
                        "WHERE ai.workspace_id = :workspace_id "
                        " AND ai.installation_key = :installation_key"
                    ),
                    parameters,
                )
            ).mappings().one()
        )
        sessions = await _session_counts(session, parameters)
        turns = await _turn_counts(session, parameters)
        grants = await _grant_counts(session, parameters)
        attempts = await _attempt_counts(session, parameters)
        last_turn_row = (
            await session.execute(
                sa.text(
                    "SELECT turn.status, turn.result_code, turn.context_message_ids "
                    "FROM agent_turns turn "
                    "JOIN agent_sessions agent_session "
                    " ON agent_session.id = turn.session_id "
                    " AND agent_session.workspace_id = turn.workspace_id "
                    "JOIN agent_deployments ad "
                    " ON ad.id = agent_session.deployment_id "
                    " AND ad.workspace_id = agent_session.workspace_id "
                    "JOIN agent_definitions definition "
                    " ON definition.id = ad.agent_definition_id "
                    " AND definition.workspace_id = ad.workspace_id "
                    "WHERE turn.workspace_id = :workspace_id "
                    " AND definition.agent_key = :agent_key "
                    "ORDER BY turn.created_at DESC, turn.id DESC LIMIT 1"
                ),
                parameters,
            )
        ).mappings().one_or_none()
        last_turn = dict(last_turn_row) if last_turn_row is not None else None
        last_attempt_row = (
            await session.execute(
                sa.text(
                    "SELECT attempt.state, attempt.error_code "
                    "FROM agent_send_attempts attempt "
                    "JOIN delivery_grants grant_row ON grant_row.id = attempt.grant_id "
                    " AND grant_row.workspace_id = attempt.workspace_id "
                    "JOIN agent_installations ai "
                    " ON ai.id = grant_row.installation_id "
                    " AND ai.workspace_id = grant_row.workspace_id "
                    "JOIN agent_deployments ad ON ad.id = ai.deployment_id "
                    " AND ad.workspace_id = ai.workspace_id "
                    "JOIN agent_definitions definition "
                    " ON definition.id = ad.agent_definition_id "
                    " AND definition.workspace_id = ad.workspace_id "
                    "WHERE attempt.workspace_id = :workspace_id "
                    " AND definition.agent_key = :agent_key "
                    "ORDER BY attempt.created_at DESC, attempt.id DESC LIMIT 1"
                ),
                parameters,
            )
        ).mappings().one_or_none()
        last_attempt = dict(last_attempt_row) if last_attempt_row is not None else None

    gates = {
        "runtime_enabled": settings.gate_universal_mayak_runtime_enabled,
        "model_data_gate_approved": (
            settings.gate_universal_mayak_model_data_gate_approved
        ),
        "delivery_enabled": settings.gate_universal_mayak_delivery_enabled,
    }
    configuration_ready = all(gates.values())
    installation_ready = (
        installation["total"] == 1 and installation["exact_active"] == 1
    )
    reconciliation_required = attempts["sending"] > 0 or attempts["unknown"] > 0
    if not workspace_valid:
        state = "workspace_unavailable"
    elif not configuration_ready:
        state = "disabled"
    elif not installation_ready:
        state = "installation_not_ready"
    elif reconciliation_required:
        state = "reconciliation_required"
    elif turns["total"] > 0:
        state = "active_observed"
    else:
        state = "ready"

    last_result_code = _redacted_code(
        last_turn.get("result_code") if last_turn is not None else None
    )
    last_attempt_state = (
        str(last_attempt["state"])
        if last_attempt is not None
        and last_attempt.get("state")
        in {"reserved", "sending", "sent", "failed", "unknown", "blocked"}
        else None
    )
    last_attempt_error_code = _redacted_code(
        last_attempt.get("error_code") if last_attempt is not None else None
    )
    input_digest_bound = _has_input_digest(last_turn)
    terminal_state = (
        last_attempt_state in _TERMINAL_ATTEMPT_STATES
        or (
            last_turn is not None
            and last_turn.get("status") == "failed"
            and last_result_code is not None
        )
    )

    return {
        "state": state,
        "workspace_ready": workspace_valid,
        "gates": gates,
        "configuration_ready": configuration_ready,
        "installation": {
            **installation,
            "ready": installation_ready,
        },
        "sessions": sessions,
        "turns": turns,
        "delivery_grants": grants,
        "send_attempts": attempts,
        "last_result_code": last_result_code,
        "last_attempt_state": last_attempt_state,
        "last_attempt_error_code": last_attempt_error_code,
        "reconciliation_required": reconciliation_required,
        "provider_free_replay_verifier": {
            "available": last_turn is not None,
            "input_digest_bound": input_digest_bound,
            "terminal_state": terminal_state,
            "verified": input_digest_bound and terminal_state,
            "provider_call_required": False,
        },
        "private_content_exposed": False,
        "provider_identifiers_exposed": False,
        "telegram_identifiers_exposed": False,
    }


async def _session_counts(
    session: AsyncSession,
    parameters: dict[str, object],
) -> dict[str, int]:
    row = (
        await session.execute(
            sa.text(
                "SELECT COUNT(*)::int AS total, "
                "COUNT(*) FILTER (WHERE agent_session.state = 'active')::int "
                " AS active, "
                "COUNT(*) FILTER (WHERE agent_session.state = 'paused')::int "
                " AS paused, "
                "COUNT(*) FILTER (WHERE agent_session.state = 'completed')::int "
                " AS completed, "
                "COUNT(*) FILTER (WHERE agent_session.state = 'handoff_required')::int "
                " AS handoff_required "
                "FROM agent_sessions agent_session "
                "JOIN agent_deployments ad ON ad.id = agent_session.deployment_id "
                " AND ad.workspace_id = agent_session.workspace_id "
                "JOIN agent_definitions definition "
                " ON definition.id = ad.agent_definition_id "
                " AND definition.workspace_id = ad.workspace_id "
                "WHERE agent_session.workspace_id = :workspace_id "
                " AND definition.agent_key = :agent_key"
            ),
            parameters,
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


async def _turn_counts(
    session: AsyncSession,
    parameters: dict[str, object],
) -> dict[str, int]:
    row = (
        await session.execute(
            sa.text(
                "SELECT COUNT(*)::int AS total, "
                "COUNT(*) FILTER (WHERE turn.status = 'queued')::int AS queued, "
                "COUNT(*) FILTER (WHERE turn.status = 'running')::int AS running, "
                "COUNT(*) FILTER (WHERE turn.status = 'proposed')::int AS proposed, "
                "COUNT(*) FILTER (WHERE turn.status = 'failed')::int AS failed "
                "FROM agent_turns turn "
                "JOIN agent_sessions agent_session "
                " ON agent_session.id = turn.session_id "
                " AND agent_session.workspace_id = turn.workspace_id "
                "JOIN agent_deployments ad ON ad.id = agent_session.deployment_id "
                " AND ad.workspace_id = agent_session.workspace_id "
                "JOIN agent_definitions definition "
                " ON definition.id = ad.agent_definition_id "
                " AND definition.workspace_id = ad.workspace_id "
                "WHERE turn.workspace_id = :workspace_id "
                " AND definition.agent_key = :agent_key"
            ),
            parameters,
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


async def _grant_counts(
    session: AsyncSession,
    parameters: dict[str, object],
) -> dict[str, int]:
    row = (
        await session.execute(
            sa.text(
                "SELECT COUNT(*)::int AS total, "
                "COUNT(*) FILTER (WHERE grant_row.used_at IS NULL "
                " AND grant_row.revoked_at IS NULL)::int AS open, "
                "COUNT(*) FILTER (WHERE grant_row.used_at IS NOT NULL)::int AS used, "
                "COUNT(*) FILTER (WHERE grant_row.revoked_at IS NOT NULL)::int "
                " AS revoked "
                "FROM delivery_grants grant_row "
                "JOIN agent_installations ai ON ai.id = grant_row.installation_id "
                " AND ai.workspace_id = grant_row.workspace_id "
                "JOIN agent_deployments ad ON ad.id = ai.deployment_id "
                " AND ad.workspace_id = ai.workspace_id "
                "JOIN agent_definitions definition "
                " ON definition.id = ad.agent_definition_id "
                " AND definition.workspace_id = ad.workspace_id "
                "WHERE grant_row.workspace_id = :workspace_id "
                " AND definition.agent_key = :agent_key"
            ),
            parameters,
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


async def _attempt_counts(
    session: AsyncSession,
    parameters: dict[str, object],
) -> dict[str, int]:
    row = (
        await session.execute(
            sa.text(
                "SELECT COUNT(*)::int AS total, "
                "COUNT(*) FILTER (WHERE attempt.state = 'reserved')::int AS reserved, "
                "COUNT(*) FILTER (WHERE attempt.state = 'sending')::int AS sending, "
                "COUNT(*) FILTER (WHERE attempt.state = 'sent')::int AS sent, "
                "COUNT(*) FILTER (WHERE attempt.state = 'failed')::int AS failed, "
                "COUNT(*) FILTER (WHERE attempt.state = 'unknown')::int AS unknown, "
                "COUNT(*) FILTER (WHERE attempt.state = 'blocked')::int AS blocked "
                "FROM agent_send_attempts attempt "
                "JOIN delivery_grants grant_row ON grant_row.id = attempt.grant_id "
                " AND grant_row.workspace_id = attempt.workspace_id "
                "JOIN agent_installations ai ON ai.id = grant_row.installation_id "
                " AND ai.workspace_id = grant_row.workspace_id "
                "JOIN agent_deployments ad ON ad.id = ai.deployment_id "
                " AND ad.workspace_id = ai.workspace_id "
                "JOIN agent_definitions definition "
                " ON definition.id = ad.agent_definition_id "
                " AND definition.workspace_id = ad.workspace_id "
                "WHERE attempt.workspace_id = :workspace_id "
                " AND definition.agent_key = :agent_key"
            ),
            parameters,
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _zero_installation() -> dict[str, int]:
    return {
        "total": 0,
        "discovered": 0,
        "pending_approval": 0,
        "active": 0,
        "paused": 0,
        "revoked": 0,
        "exact_active": 0,
    }


def _zero_states(states: tuple[str, ...]) -> dict[str, int]:
    return {"total": 0, **{state: 0 for state in states}}


def _has_input_digest(last_turn: dict[str, Any] | None) -> bool:
    if last_turn is None:
        return False
    values = last_turn.get("context_message_ids")
    if not isinstance(values, list):
        return False
    return any(
        isinstance(value, str)
        and value.startswith(_INPUT_DIGEST_PREFIX)
        and len(value.removeprefix(_INPUT_DIGEST_PREFIX)) == 64
        and all(
            character in "0123456789abcdef"
            for character in value.removeprefix(_INPUT_DIGEST_PREFIX)
        )
        for value in values
    )


def _redacted_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    bounded = value.strip()[:128]
    if not bounded or not bounded.startswith(("mayak_", "telegram_")):
        return None
    if any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_:-"
        for character in bounded
    ):
        return None
    return bounded
