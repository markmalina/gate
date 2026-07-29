"""Company-safe operational telemetry for the owner-only Universal Mayak.

Only configuration booleans, aggregate counts and bounded internal result codes
leave this module. No message, prompt, provider identifier, Telegram identifier,
person identity, secret or candidate text is selected.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

_AGENT_KEY = "oko"
_REVISION_KEY = "universal-mayak-oko-v2"
_INSTALLATION_KEY = "universal-mayak-v1-owner-private"
_TERMINAL_ATTEMPT_STATES = frozenset({"sent", "failed", "unknown", "blocked"})
_INPUT_DIGEST_PREFIX = "mayak-input-sha256:"
_OWNER_ACCEPTANCE_TASK_ID = "4f060e00-0bb5-4701-8f38-80b06da9fc8e"
_OWNER_ACCOUNT_ID = "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2"
_OWNER_ACCOUNT_ALIAS = "telegram_mark_by"
_SOURCE_PROJECT_ID = "e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0"
_TARGET_PROJECT_ID = "f2659ed6-9a1d-5f72-8bf1-fe45420cfef7"
_ROUTE_PROBE_FIELDS = (
    "task_present",
    "account_ready",
    "trigger_message_present",
    "projects_allowed",
    "canonical_ready",
    "private_inbound_ready",
    "actor_dialog_ready",
    "connector_ready",
    "business_connection_ready",
    "release_ready",
)


async def universal_mayak_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    settings: Settings,
) -> dict[str, Any]:
    """Return one redacted snapshot scoped to the exact Oko agent definition."""

    workspace_value = await session.scalar(
        sa.text(
            "SELECT id FROM workspaces "
            "WHERE key = :workspace_key AND is_active IS TRUE"
        ).bindparams(workspace_key=workspace_key)
    )
    workspace_id = _uuid_or_none(workspace_value)
    workspace_valid = workspace_id is not None

    installation = _zero_installation()
    sessions = _zero_states(("active", "paused", "completed", "handoff_required"))
    turns = _zero_states(("queued", "running", "proposed", "failed"))
    grants = _zero_states(("open", "used", "revoked"))
    attempts = _zero_states(
        ("reserved", "sending", "sent", "failed", "unknown", "blocked")
    )
    last_turn: dict[str, Any] | None = None
    last_attempt: dict[str, Any] | None = None
    owner_route_probe = _zero_route_probe()

    if workspace_id is not None:
        parameters: dict[str, object] = {
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
                    ).bindparams(**parameters),
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
                ).bindparams(**_agent_scope(parameters)),
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
                ).bindparams(**_agent_scope(parameters)),
            )
        ).mappings().one_or_none()
        last_attempt = dict(last_attempt_row) if last_attempt_row is not None else None
        owner_route_probe = await _owner_route_probe(session, workspace_id)

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
        "owner_route_probe": owner_route_probe,
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


def _zero_route_probe() -> dict[str, bool]:
    return {**{field: False for field in _ROUTE_PROBE_FIELDS}, "ready": False}


async def _owner_route_probe(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, bool]:
    """Evaluate exact route guards without selecting content or identifiers."""

    row = (
        await session.execute(
            sa.text(
                "SELECT "
                "(rt.id IS NOT NULL) AS task_present, "
                "(a.id = :account_id AND a.alias = :account_alias "
                " AND a.is_active IS TRUE AND a.read_enabled IS TRUE "
                " AND a.send_enabled IS TRUE "
                " AND rt.account_id = :account_id "
                " AND m.account_id = :account_id) AS account_ready, "
                "(m.id IS NOT NULL) AS trigger_message_present, "
                "(rt.project_id IN (:source_project_id, :target_project_id) "
                " AND m.project_id IN "
                " (:source_project_id, :target_project_id)) AS projects_allowed, "
                "(cm.id IS NOT NULL AND cm.workspace_id = :workspace_id "
                " AND d.workspace_id = :workspace_id "
                " AND c.workspace_id = :workspace_id) AS canonical_ready, "
                "(cm.direction = 'inbound' AND m.direction = 'inbound' "
                " AND d.dialog_kind = 'private') AS private_inbound_ready, "
                "(cm.provider_sender_id = d.provider_dialog_id "
                " AND m.chat_id = d.provider_dialog_id "
                " AND m.sender_id = d.provider_dialog_id) AS actor_dialog_ready, "
                "(c.connector_kind = 'telegram_business' "
                " AND c.is_enabled IS TRUE) AS connector_ready, "
                "(bc.id IS NOT NULL "
                " AND bc.is_enabled IS TRUE) AS business_connection_ready, "
                "EXISTS (SELECT 1 FROM agent_deployments ad "
                " JOIN agent_definitions definition "
                " ON definition.id = ad.agent_definition_id "
                " AND definition.workspace_id = ad.workspace_id "
                " JOIN instruction_revisions ir "
                " ON ir.id = ad.instruction_revision_id "
                " AND ir.workspace_id = ad.workspace_id "
                " WHERE ad.workspace_id = :workspace_id "
                " AND ad.deployment_key = :deployment_key "
                " AND ad.state IN ('staged', 'active') "
                " AND definition.agent_key = :agent_key "
                " AND ir.revision_key = :revision_key "
                " AND ir.validation_state = 'validated') AS release_ready "
                "FROM (SELECT 1) anchor "
                "LEFT JOIN reply_tasks rt ON rt.id = :task_id "
                "LEFT JOIN messages m ON m.id = rt.trigger_message_id "
                "LEFT JOIN accounts a ON a.id = rt.account_id "
                "LEFT JOIN canonical_messages cm ON cm.id = m.canonical_message_id "
                "LEFT JOIN dialogs d ON d.id = cm.dialog_id "
                "LEFT JOIN transport_connectors c ON c.id = d.connector_id "
                "LEFT JOIN telegram_business_connections bc "
                " ON bc.account_id = a.id "
                " AND bc.business_connection_id = m.business_connection_id"
            ).bindparams(
                workspace_id=workspace_id,
                task_id=uuid.UUID(_OWNER_ACCEPTANCE_TASK_ID),
                account_id=uuid.UUID(_OWNER_ACCOUNT_ID),
                account_alias=_OWNER_ACCOUNT_ALIAS,
                source_project_id=uuid.UUID(_SOURCE_PROJECT_ID),
                target_project_id=uuid.UUID(_TARGET_PROJECT_ID),
                deployment_key="universal-mayak-oko-v2-openai",
                agent_key=_AGENT_KEY,
                revision_key=_REVISION_KEY,
            )
        )
    ).mappings().one()
    probe = {field: bool(row.get(field)) for field in _ROUTE_PROBE_FIELDS}
    return {**probe, "ready": all(probe.values())}


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
            ).bindparams(**_agent_scope(parameters)),
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
            ).bindparams(**_agent_scope(parameters)),
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
            ).bindparams(**_agent_scope(parameters)),
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
            ).bindparams(**_agent_scope(parameters)),
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _agent_scope(parameters: dict[str, object]) -> dict[str, object]:
    return {
        "workspace_id": parameters["workspace_id"],
        "agent_key": parameters["agent_key"],
    }


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


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
